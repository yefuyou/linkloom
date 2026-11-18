"""LangGraph orchestration of a dynamic multi-agent investigation.

Topology (compiled once, never changed at runtime; the task DAG is data in the state):

    START -> supervisor -> [confirm] -> scheduler -> worker (Send, parallel) -> scheduler ... -> synthesize -> END
                 ^                          |
                 +---- review (gaps) -------+

* supervisor: LLM tool call proposes tasks; the Host validates and may fall back or stop.
* scheduler: Command + Send dispatches only dependency-ready tasks, at most max_concurrency per wave.
* worker: a compiled subgraph wrapping RuntimeEngine and the Reviewer gate, one task each.
* confirm: langgraph interrupt, used when a plan exceeds the Host's confirmation threshold.
* synthesize: existing merge_evidence / synthesize_reviewed over every reviewed result.

The LangGraph checkpointer (SqliteSaver) owns orchestration state and resume. Runtime checkpoints are
Worker-internal. See worker.py for how a replayed Worker avoids a second paid call.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from time import time
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from linkloom.orchestration import blackboard
from linkloom.orchestration.files import write_json
from linkloom.orchestration.state import (
    ABSTAINED, ACCEPTED, FAILED, PENDING, RUNNING, SKIPPED, TERMINAL, OrchestratorState,
)
from linkloom.orchestration.supervisor import (
    ANSWER_SINGLE, DELEGATE, STOP, DelegationRejected, accept_delegation, delegation_request, single_task,
    tool_arguments,
)
from linkloom.orchestration.worker import (
    HARD_FAILURES, WorkerDeps, build_worker_graph, close_session, paid_without_receipt)
from linkloom.orchestration.comparison import compare_pending
from linkloom.ui.branch_synthesis import merge_evidence, plan_comparison, resolve_pairs, synthesize_reviewed
from linkloom.ui.task_provider import TaskProviderSession


def open_checkpointer(path):
    """SqliteSaver on a file: orchestration state survives a process restart."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None


def _context(state):
    """What a model may see of the Host input. The Host's own deny list names other workspaces; it stays out."""
    scope = {key: value for key, value in (state.get('confirmed_scope') or {}).items() if key != 'blocked_entities'}
    return dict(confirmed_scope=scope, entity_leads=list(state.get('entity_leads') or []))


def _summary(state):
    rows = state.get('rows') or {}
    out = []
    for task_id, task in (state.get('tasks') or {}).items():
        item = dict(id=task_id, description=task['description'], depends_on=task['depends_on'],
                    status=task['status'], failure=task.get('failure'))
        if task_id in rows:
            item['verified_decision'] = rows[task_id]['conclusion']['decision']['value']
            item['gaps'] = blackboard.gaps_of(rows[task_id])
        out.append(item)
    return out


def _has_gaps(state):
    rows = state.get('rows') or {}
    return any(task['status'] != ACCEPTED or blackboard.gaps_of(rows[task_id])
               for task_id, task in (state.get('tasks') or {}).items())


def build_graph(deps: WorkerDeps, checkpointer):
    limits = deps.limits
    worker = build_worker_graph(deps)

    def decide(state, round_number):
        """One Supervisor model call, paid at most once per round: the raw proposal is kept as a receipt."""
        root = deps.root / 'supervisor' / f'round-{round_number}'
        saved = _read(root / 'proposal.json')
        if saved is not None:
            return saved
        if paid_without_receipt(root):  # killed after a request may have been sent: never pay this round twice
            saved = dict(payload=None, error='SUPERVISOR_FAILED', failure='PROVIDER_UNCERTAIN')
            write_json(root / 'proposal.json', saved)
            return saved
        context = dict(user_goal=state['query'], **_context(state), tasks=_summary(state),
                       limits=dict(max_tasks=limits.task_cap(), max_depth=limits.max_depth),
                       round=round_number)
        session = TaskProviderSession(root, limits.supervisor, deps.validate)
        try:
            request = delegation_request(state['query'], context, round_number)
            arguments = tool_arguments(session.bind(deps.model_factory(state['query']), 'supervisor').complete(request))
            saved = dict(payload=arguments, error=None)
        except DelegationRejected as exc:
            saved = dict(payload=None, error=str(exc))
        except Exception:
            saved = dict(payload=None, error='SUPERVISOR_FAILED')
        finally:
            close_session(session)
        saved['failure'] = session.failure
        write_json(root / 'proposal.json', saved)
        return saved

    def supervisor(state) -> Command[Literal['confirm', 'scheduler', 'synthesize']]:
        existing = state.get('tasks') or {}
        round_number = state.get('round', 0)
        saved = decide(state, round_number)
        update = dict(round=round_number + 1, review_pending=False)
        if saved.get('failure') in HARD_FAILURES:
            return Command(update=dict(update, abort_reason=saved['failure'], trace=[
                dict(event='supervisor_aborted', round=round_number, failure=saved['failure'])]), goto='synthesize')
        blocked = (state.get('confirmed_scope') or {}).get('blocked_entities', ())
        try:
            if saved['error']:
                raise DelegationRejected(saved['error'])
            action, reason, new = accept_delegation(saved['payload'], query=state['query'], existing=existing,
                limits=limits, blocked=blocked, round_number=round_number)
        except DelegationRejected as exc:
            # Fail closed: a first plan that cannot be trusted becomes one task for the original goal;
            # a review that cannot be trusted adds no paid work.
            action, reason, new = (ANSWER_SINGLE, str(exc), single_task(state['query'], 'HOST_FALLBACK_' + str(exc))) \
                if not existing else (STOP, str(exc), {})
        event = dict(event='supervisor_decision', round=round_number, action=action, reason=reason,
                     tasks=sorted(new))
        update['trace'] = [event]
        if action == STOP:
            return Command(update=update, goto='synthesize')
        update['tasks'] = new
        threshold = limits.confirm_over_tasks
        if action == DELEGATE and threshold is not None and len(new) > threshold:
            update['confirmation'] = dict(status='pending', task_ids=sorted(new))
            return Command(update=update, goto='confirm')
        return Command(update=update, goto='scheduler')

    def confirm(state) -> Command[Literal['scheduler', 'synthesize']]:
        pending = state['confirmation']
        plan = [dict(id=task_id, description=state['tasks'][task_id]['description'],
                     depends_on=state['tasks'][task_id]['depends_on']) for task_id in pending['task_ids']]
        answer = interrupt(dict(kind='delegation_confirmation', tasks=plan))
        approved = isinstance(answer, dict) and answer.get('approved') is True
        if approved:
            decision = dict(kind='delegation_plan', task_ids=pending['task_ids'],
                            reviewer_id=str(answer.get('reviewer_id') or ''), reviewer_identity='self_asserted', reason=str(answer.get('reason') or ''))
            return Command(update=dict(confirmation=dict(pending, status='approved'),
                                       approved_decisions=[decision],
                                       trace=[dict(event='plan_approved', tasks=pending['task_ids'])]),
                           goto='scheduler')
        rejected = {task_id: dict(state['tasks'][task_id], status=SKIPPED, failure='PLAN_REJECTED')
                    for task_id in pending['task_ids']}
        return Command(update=dict(confirmation=dict(pending, status='rejected'), tasks=rejected,
                                   trace=[dict(event='plan_rejected', tasks=pending['task_ids'])]),
                       goto='scheduler')

    def scheduler(state, config: RunnableConfig) -> Command[Literal['worker', 'supervisor', 'synthesize']]:
        tasks = {task_id: dict(task) for task_id, task in state['tasks'].items()}
        dispatches = state.get('dispatches', 0)
        abort = state.get('abort_reason')
        # Each invocation (start or a resume after a restart) gets its own deadline window.
        expired = time() >= config['configurable'].get('deadline_at', state['deadline_at'])
        stop_reason = 'ABORTED' if abort else 'DEADLINE' if expired else None
        left = limits.dispatch_cap() - dispatches
        changed = {}

        def put(task, **fields):
            task.update(fields)
            changed[task['id']] = task

        budget_failure = 'TASK_PROVIDER_BUDGET_EXHAUSTED'
        for task in tasks.values():  # retry a retryable failure as a new paid attempt
            if (task['status'] == FAILED and task.get('retryable') and task.get('failure') != budget_failure
                    and task['attempt'] < limits.max_attempts
                    and not stop_reason and left > 0):
                put(task, status=PENDING, epoch=task['epoch'] + 1, failure=None, retryable=False)
        progressed = True
        while progressed:
            progressed = False
            for task in tasks.values():
                if task['status'] != PENDING:
                    continue
                dead = [dep for dep in task['depends_on'] if tasks[dep]['status'] in (ABSTAINED, FAILED, SKIPPED)]
                if stop_reason or dead:
                    put(task, status=SKIPPED, failure=stop_reason or 'UPSTREAM_UNAVAILABLE')
                    progressed = True
        ready = sorted((task for task in tasks.values() if task['status'] == PENDING
                        and all(tasks[dep]['status'] == ACCEPTED for dep in task['depends_on'])),
                       key=lambda task: (task['depth'], task['id']))
        batch = ready[:max(0, min(limits.max_concurrency, left))]
        if ready and not batch:
            for task in ready:
                put(task, status=SKIPPED, failure='BUDGET_EXHAUSTED')
        if batch:
            sends = []
            rows = state.get('rows') or {}
            for task in batch:
                put(task, status=RUNNING, attempt=task['attempt'] + 1)
                upstream = [blackboard.upstream_result(dep, rows[dep]) for dep in task['depends_on']]
                sends.append(Send('worker', dict(task=dict(task), upstream=upstream, context=_context(state))))
            update = dict(tasks=dict(changed), dispatches=dispatches + len(batch), trace=[
                dict(event='wave_dispatched', tasks=[task['id'] for task in batch])])
            return Command(update=update, goto=sends)
        update = dict(tasks=dict(changed)) if changed else {}
        merged = dict(state, tasks={**state['tasks'], **changed})
        # A review can propose equivalent work under a new ID, but it cannot enlarge the Worker slice.
        budget_exhausted = any(task['status'] == FAILED and task.get('failure') == budget_failure
                               for task in tasks.values())
        reviewable = (not stop_reason and not budget_exhausted and state.get('round', 0) <= limits.max_rounds
                      and len(tasks) < limits.task_cap() and left > 0 and _has_gaps(merged)
                      and not any(task['status'] in (PENDING, RUNNING) for task in tasks.values()))
        return Command(update=update, goto='supervisor' if reviewable else 'synthesize')

    def synthesize(state):
        tasks, rows = state.get('tasks') or {}, state.get('rows') or {}
        if state.get('abort_reason'):
            return dict(status='ABORTED', answer=None, trace=[dict(event='synthesis_withheld',
                                                                      reason=state['abort_reason'])])
        reviewed = [rows[task_id] for task_id in tasks if task_id in rows]
        gaps = {task_id: ('SKIPPED' if task['status'] == SKIPPED else 'FAILED')
                for task_id, task in tasks.items() if task['status'] in (FAILED, SKIPPED)}
        merged = merge_evidence(reviewed)
        questions = {task_id: task['description'] for task_id, task in tasks.items()}
        # Structure decides first; only pairs it cannot decide cost the one comparison call (a receipt, never resent).
        plan = plan_comparison(reviewed, {task_id: task['depends_on'] for task_id, task in tasks.items()})
        verdicts, reason = compare_pending(deps, plan)
        comparison = resolve_pairs(plan, verdicts, reason or 'COMPARISON_FAILED')
        answer = synthesize_reviewed(reviewed, gaps, 0, merged, questions, source_check=deps.validate,
                                     review_id=f'{deps.run_id}-aggregate', comparison=comparison)
        status = answer['status'] if answer.get('published') else 'UNKNOWN'
        return dict(status=status, answer=answer, trace=[dict(event='synthesized', status=status)])

    graph = StateGraph(OrchestratorState)
    graph.add_node('supervisor', supervisor)
    graph.add_node('confirm', confirm)
    graph.add_node('scheduler', scheduler, destinations=('worker', 'supervisor', 'synthesize'))
    graph.add_node('worker', worker)
    graph.add_node('synthesize', synthesize)
    graph.add_edge(START, 'supervisor')
    graph.add_edge('worker', 'scheduler')
    graph.add_edge('synthesize', END)
    return graph.compile(checkpointer=checkpointer)


class Orchestrator:
    """Runs and resumes orchestration runs. One LangGraph thread per run_id."""

    def __init__(self, deps: WorkerDeps, checkpointer):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)

    def _config(self):
        limits = self.deps.limits
        return {'configurable': {'thread_id': self.deps.run_id, 'deadline_at': time() + limits.deadline_seconds},
                'max_concurrency': limits.max_concurrency,
                'recursion_limit': 30 + 4 * limits.dispatch_cap() + 4 * limits.max_rounds}

    def start(self, query, *, confirmed_scope=None, entity_leads=()):
        if self.graph.get_state(self._config()).values:
            return self.resume()
        return self.graph.invoke(dict(
            query=query, confirmed_scope=deepcopy(confirmed_scope or {}), entity_leads=list(entity_leads),
            deadline_at=time() + self.deps.limits.deadline_seconds, tasks={}, rows={}, raw_retrievals={},
            verified_evidence={}, inferences={}, approved_decisions=[], round=0, dispatches=0, trace=[],
            confirmation=None, abort_reason=None, status='RUNNING', answer=None), self._config())

    def resume(self, approval=None):
        """Continue from the last LangGraph checkpoint. approval answers a pending confirmation interrupt."""
        return self.graph.invoke(Command(resume=approval) if approval is not None else None, self._config())

    def state(self):
        snapshot = self.graph.get_state(self._config())
        return dict(snapshot.values, next=list(snapshot.next),
                    interrupts=[item.value for task in snapshot.tasks for item in task.interrupts])

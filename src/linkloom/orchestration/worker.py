"""Worker subgraph: one task, own session, own Runtime checkpoint, own result.

A Worker wraps the existing RuntimeEngine.start_multi_agent and the existing Reviewer projection
(build_branch_row). Failure and abstention are task statuses, never exceptions that stop other Workers.

Recovery responsibility: the LangGraph checkpoint decides which tasks still need a Worker. The Runtime
checkpoint under tasks/<id>/attempt-<n>/runtime is only this Worker's execution record. Two small receipts
(status.json after the paid Runtime call, row.json after the Reviewer gate) make a replayed Worker a no-op,
so a task whose paid call finished is never paid for twice.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from linkloom.orchestration import blackboard
from linkloom.orchestration.files import write_json
from linkloom.orchestration.limits import OrchestrationLimits
from linkloom.orchestration.state import ABSTAINED, ACCEPTED, FAILED, WorkerState
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest, RunStatus
from linkloom.ui.backend import project_reviewer
from linkloom.ui.projection import collect_observed_evidence, project_run_snapshot
from linkloom.ui.lifecycle import BackgroundShutdownTimeout
from linkloom.ui.task_provider import TaskProviderSession

HARD_FAILURES = frozenset({'MODEL_AUTH_REQUIRED', 'MODEL_BILLING_BLOCKED', 'TASK_SOURCE_AUTHORIZATION_CHANGED'})
UNCERTAIN_FAILURES = frozenset({'PROVIDER_TIMEOUT_UNCERTAIN', 'PROVIDER_UNCERTAIN'})
_NO_DECISION = ('insufficient_evidence', 'not_found')


@dataclass
class WorkerDeps:
    """Host objects a Worker needs. They stay out of the LangGraph state (they are not serializable)."""
    root: Path
    run_id: str
    vault: Any
    index: Any
    workspace: Any                      # WorkspacePresentationContext
    workspace_id: str
    source_names: dict
    source_versions: dict
    model_factory: Callable[[str], Any]
    validate: Callable[[], None]
    limits: OrchestrationLimits


class OutputBoundedAdapter:
    """Caps one request's output tokens at the Worker's slice, never raises the model's own limit."""

    def __init__(self, adapter, output_limit):
        self.adapter = adapter
        self.output_limit = output_limit
        self.capability = getattr(adapter, 'capability', None)

    def complete(self, request):
        options = request.generation_options
        output = min(options.max_output_tokens or self.output_limit, self.output_limit)
        return self.adapter.complete(replace(request, generation_options=replace(options, max_output_tokens=output)))


def build_branch_row(name, question, status, checkpoint_dir, checkpointer, workspace, source_versions, source_names):
    """Turn one completed Worker Runtime result into a task row (run, evidence, conclusion).

    The Reviewer projection must accept the artifact, and every evidence item must carry the source identity,
    version and hashes the Runtime observed. A row that cannot be built is a failed task, never a result.
    """
    if status.status != 'completed' or not status.result_ref:
        raise ValueError('WORKER_RUNTIME_FAILED')
    checkpoint_dir = Path(checkpoint_dir)
    result_path = (checkpoint_dir / status.result_ref).resolve()
    if not result_path.is_relative_to(checkpoint_dir.resolve()):
        raise ValueError('WORKER_RUNTIME_FAILED')
    artifact = json.loads(result_path.read_text(encoding='utf-8'))
    review = project_reviewer(artifact)
    if review['decision'] != 'evidence_sufficient':
        raise ValueError('WORKER_REVIEW_FAILED')
    state = checkpointer.load(status.thread_id)
    snapshot = project_run_snapshot(query=question, workspace=workspace, status=status,
        result_artifact=artifact, tool_ledger=state.tool_ledger)
    observed = collect_observed_evidence(state.tool_ledger)
    evidence = [dict(item, **source_versions[item['relative_path']],
                     source_name=source_names[item['relative_path']],
                     content_sha256=observed[item['ref']]['content_sha256'],
                     quote_sha256=observed[item['ref']]['quote_sha256'])
                for item in snapshot['evidence']]
    if not evidence and snapshot['kind'] != 'insufficient':
        raise ValueError('WORKER_EVIDENCE_MISSING')
    team = artifact['result']['team_decision']
    conclusion = dict(question=question, decision=dict(value=team['decision']['value'],
                          status=team['decision']['status']),
                      unresolved_items=[item['description'] for item in team['unresolved_items']],
                      uncertainty=dict(status=team['uncertainty']['status'],
                                       statement=team['uncertainty']['statement']),
                      evidence=[{key: item[key] for key in ('source_id', 'source_version', 'line_start',
                                                            'line_end', 'quote_sha256')}
                                for item in evidence],
                      # The key is the Host-held task text, never model output: equal keys mean the same
                      # question was assigned twice, and then differing answers are reported as a conflict.
                      claim_key=dict(subject=question, relation='subgoal_question'),
                      claim_value=team['decision']['value'])
    return dict(run=dict(branch=name, run_id=status.run_id, thread_id=status.thread_id,
                         review_id=review['review_id']), evidence=evidence, conclusion=conclusion)


def render_query(task, upstream, context):
    """The Runtime question: the task text plus the structured input the Host allows this Worker to see."""
    structured = {}
    if context.get('confirmed_scope'):
        structured['confirmed_scope'] = context['confirmed_scope']
    if context.get('entity_leads'):
        structured['entity_leads_unverified'] = context['entity_leads']
    if upstream:
        structured['verified_upstream_results'] = upstream
    if not structured:
        return task['description']
    return task['description'] + '\n\ninvestigation_context=' + json.dumps(structured, ensure_ascii=False)


def attempt_dir(deps, task):
    return deps.root / 'tasks' / task['id'] / f"attempt-{task['epoch']}-{task['attempt']}"


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None


def paid_without_receipt(root):
    """True when a previous process reserved provider requests in this directory and left no receipt.

    The budget journal is fsynced before each request crosses the transport, so reserved requests with no
    receipt mean the process died while a paid call may have been in flight. Its outcome is unknown, and it
    is never sent again."""
    journal = _read(Path(root) / 'budget.json')
    return isinstance(journal, dict) and (journal.get('requests_reserved') or 0) > 0


def close_session(session, timeout=1.0):
    """Stop the session accepting calls and wait briefly for its in-flight provider thread.

    A call still running after the wait cannot be killed. Its outcome is unknown, so the session is marked
    PROVIDER_UNCERTAIN (kept if it already carries a failure) and the attempt is never sent again.
    Returns True when nothing is left running."""
    try:
        session.close(timeout)
        return True
    except BackgroundShutdownTimeout:
        if session.failure is None:
            session.failure = 'PROVIDER_UNCERTAIN'
        session._save()
        return False


def _run(deps, task, query):
    root = attempt_dir(deps, task)
    if paid_without_receipt(root):
        receipt = dict(run_status=None, failure='PROVIDER_UNCERTAIN', error=None)
        write_json(root / 'status.json', receipt)
        return receipt
    limits = deps.limits.per_task
    session = TaskProviderSession(root, limits, deps.validate)
    status, error = None, None
    try:
        turns = 3
        if limits.max_requests < turns or limits.max_output_tokens < turns:
            session._error('TASK_PROVIDER_BUDGET_EXHAUSTED')
            raise ValueError('WORKER_BUDGET_INSUFFICIENT')
        turns = min(turns + 1, limits.max_requests, limits.max_output_tokens)
        adapter = OutputBoundedAdapter(session.bind(deps.model_factory(task['description']), 'runtime'),
                                        min(OrchestrationLimits.WORKER_MAX_OUTPUT_PER_REQUEST,
                                            limits.max_output_tokens // turns))
        engine = RuntimeEngine(vault_root=deps.vault, index_path=deps.index,
            checkpoint_dir=root / 'runtime', trace_dir=root / 'traces', model=adapter,
            memory_enabled=False, decision_memory_enabled=False,
            decision_memory_workspace_id=deps.workspace_id)
        thread = f"{deps.run_id}-{task['id']}-{task['epoch']}-{task['attempt']}"
        status = engine.start_multi_agent(RunRequest(request_id=thread, thread_id=thread, query=query,
            workflow='team_decision', task_plan=None, planning_mode='template', max_steps=turns,
            max_provider_requests=turns, dry_run=True, caller='product_ui', reserve_final_turn=True,
            cited_answer=True))
    except Exception as exc:
        error = str(exc) if isinstance(exc, ValueError) and str(exc).isupper() else 'WORKER_RUNTIME_FAILED'
    finally:  # normal end, timeout, cancel or a BaseException: no provider thread is left owned by nobody
        close_session(session)
    receipt = dict(run_status=status.to_dict() if status else None, failure=session.failure, error=error)
    write_json(root / 'status.json', receipt)
    return receipt


def _gate(deps, task, receipt):
    """Reviewer gate: turn a completed Runtime result into a verified row, or a failure code."""
    root = attempt_dir(deps, task)
    status = RunStatus.from_dict(receipt['run_status']) if receipt.get('run_status') else None
    observed = {}
    if status is not None:
        try:
            state = SQLiteCheckpointer(root / 'runtime').load(status.thread_id)
            observed = collect_observed_evidence(state.tool_ledger)
        except Exception:
            observed = {}
    code = receipt.get('failure') or receipt.get('error')
    if code is None and status is not None and status.status != 'completed':
        code = (status.error or {}).get('code') or 'WORKER_RUNTIME_FAILED'
    if code is None and status is not None:
        try:
            row = build_branch_row(task['id'], task['description'], status, root / 'runtime',
                                   SQLiteCheckpointer(root / 'runtime'), deps.workspace,
                                   deps.source_versions, deps.source_names)
            write_json(root / 'row.json', row)
            return row, observed, None
        except ValueError as exc:
            code = str(exc) if str(exc).isupper() else 'WORKER_GATE_FAILED'
        except Exception:
            code = 'WORKER_GATE_FAILED'
    return None, observed, code or 'WORKER_RUNTIME_FAILED'


def _abstained(row):
    decision = row['conclusion']['decision']
    return not decision.get('value') or decision.get('status') in _NO_DECISION


def build_worker_graph(deps):
    def prepare(state):
        task = state['task']
        root = attempt_dir(deps, task)
        return dict(receipt=_read(root / 'status.json'), outcome_row=_read(root / 'row.json'))

    def run(state):
        task = state['task']
        if state.get('outcome_row') is not None or state.get('receipt') is not None:
            return {}  # replay: the paid call already finished
        query = render_query(task, state.get('upstream', []), state.get('context', {}))
        return dict(receipt=_run(deps, task, query))

    def gate(state):
        task, row, observed, code = state['task'], state.get('outcome_row'), {}, None
        if row is None:
            row, observed, code = _gate(deps, task, state['receipt'])
        elif state.get('receipt'):
            _, observed, _ = _gate(deps, task, dict(state['receipt'], failure=None, error='REPLAY'))
        return dict(outcome_row=row, outcome_code=code, outcome_observed=observed)

    def emit(state):
        task, row, code = state['task'], state.get('outcome_row'), state.get('outcome_code')
        out = dict(task, attempt=task['attempt'])
        update = dict(raw_retrievals=blackboard.raw_entries(task['id'], state.get('outcome_observed') or {}))
        if row is not None:
            status = ABSTAINED if _abstained(row) else ACCEPTED
            result_ref = (attempt_dir(deps, task) / 'row.json').relative_to(deps.root).as_posix()
            out.update(status=status, result_ref=result_ref, failure=None if status == ACCEPTED else 'NO_DECISION')
            update.update(rows={task['id']: row},
                          verified_evidence=blackboard.verified_entries(task['id'], row['evidence']),
                          inferences={task['id']: blackboard.inference_entry(task['id'], row)})
        else:
            hard = code in HARD_FAILURES
            out.update(status=FAILED, failure=code,
                       retryable=not hard and code not in UNCERTAIN_FAILURES)
            if hard:
                update['abort_reason'] = code
        update['tasks'] = {task['id']: out}
        update['trace'] = [dict(event='worker_finished', task=task['id'], status=out['status'],
                                attempt=task['attempt'], failure=out.get('failure'))]
        return update

    graph = StateGraph(WorkerState)
    for name, node in (('prepare', prepare), ('run', run), ('gate', gate), ('emit', emit)):
        graph.add_node(name, node)
    graph.add_edge(START, 'prepare')
    graph.add_edge('prepare', 'run')
    graph.add_edge('run', 'gate')
    graph.add_edge('gate', 'emit')
    graph.add_edge('emit', END)
    return graph.compile()

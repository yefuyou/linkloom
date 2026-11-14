"""Run two independent portions of a trusted plan through the existing Runtime."""

from copy import deepcopy
from dataclasses import replace
import json
from threading import Event
from time import monotonic

from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest, RunStatus
from linkloom.runtime.task_plan import TaskPlan, validate_model_proposed_plan
from linkloom.ui.backend import project_reviewer
from linkloom.ui.branch_budget import _HARD_FAILURES, BranchBudgetPool, CANCELLED, HARD_ABORT
from linkloom.ui.projection import collect_observed_evidence, project_run_snapshot


def independent_subplans(plan, max_steps):
    if plan is None or 2 * len(plan.subgoals) + 1 > max_steps:
        return ()
    plan = validate_model_proposed_plan(plan.to_dict(), user_query=plan.goal, max_steps=max_steps)
    roots = {}
    groups = {}
    for goal in plan.subgoals:
        parents = {roots[parent] for parent in goal.depends_on}
        if len(parents) > 1:
            return ()
        root = next(iter(parents), goal.subgoal_id)
        roots[goal.subgoal_id] = root
        groups.setdefault(root, []).append(goal)
    if len(groups) != 2:
        return ()
    return tuple(
        (goals[0].question, TaskPlan(goals[0].question, tuple(goals), plan.schema_version)
         if len(goals) > 1 else None)
        for goals in groups.values()
    )


class _OutputBoundedAdapter:
    def __init__(self, adapter, output_limit):
        self.adapter = adapter
        self.output_limit = output_limit
        self.capability = getattr(adapter, 'capability', None)

    def complete(self, request):
        options = request.generation_options
        output = min(options.max_output_tokens or self.output_limit, self.output_limit)
        return self.adapter.complete(replace(request, generation_options=replace(options, max_output_tokens=output)))


class ParallelInvestigation:
    def __init__(self, root, run_id, query, workspace, limits, validate, subplans, model_factory,
                 vault, index, source_names, source_versions, workspace_id, planner=False):
        self.query = query
        self.workspace = workspace
        self.run_id = run_id
        self.vault = vault
        self.index = index
        self.source_versions = source_versions
        self.source_names = source_names
        self.workspace_id = workspace_id
        self.subplans = subplans
        self.model_factory = model_factory
        # With a Planner, one extra declared slot is billed in the same pool and never published.
        # It reserves only what one Planner request can use, not an equal third of the pool.
        reserve = {}
        if planner:
            from linkloom.ui.branch_planner import planner_reserve
            reserve = {'planner': planner_reserve(query, limits)}
        self.pool = BranchBudgetPool(root, limits, validate, run_id, 2 + 1 if planner else len(subplans),
                                     aux=reserve)
        self.outcome = None
        self._answer = None
        self.planner = None  # None: no Planner; else dict(source=LLM|TEMPLATE|NONE, reason=...)
        self._planner_enabled, self._planned = planner, False

    def plan(self, as_of, max_steps, blocked_entities):
        """Call the Planner once; keep the template split unless the Host accepts the model plan."""
        if not self._planner_enabled or self._planned:
            raise RuntimeError('PLANNER_CALL_NOT_ALLOWED')
        self._planned = True
        from linkloom.ui.branch_planner import PLANNER_OUTPUT_TOKENS, PlanRejected, PlannerScope, plan_branches
        found, done, bound = {}, Event(), {}
        try:  # read on the caller's thread: the store lock is held by the request, not by the pool thread
            blocked = tuple(blocked_entities())  # a missing provider fails closed
        except Exception:
            blocked = None
        # A branch can only use what the pool still has after the Planner took its one request.
        total = self.pool.total
        branch_requests = (total.max_requests - 1) // 2
        branch_output = (total.max_output_tokens - PLANNER_OUTPUT_TOKENS) // 2

        def work(session):
            try:
                bound['session'] = session
                scope = PlannerScope(self.query, as_of, max_steps, blocked, branch_requests, branch_output)
                found['plan'] = plan_branches(session.bind(self.model_factory(self.query), 'plan'), scope)
            except PlanRejected as error:
                found['reason'] = str(error)
            except Exception:
                found['reason'] = 'PLANNER_FAILED'
            finally:
                done.set()
            return dict(planned='plan' in found)

        # The request thread may hold the store lock that the parent validation needs. Validate on this
        # thread before and after the call; the pool thread skips it while this thread waits.
        parent, status = self.pool.parent_validate, 'CONTEXT_UNAVAILABLE'
        try:
            if blocked is not None:
                parent()
                self.pool.parent_validate = lambda: None
                # The Planner has its own shorter time limit (session and request). The session enforces it,
                # so done is always set; a timeout is a Planner-local failure and never stops the pool.
                status = self.pool.launch('planner', work, max_seconds=total.max_seconds / 4)
                if status == 'RUNNING':
                    done.wait()
                    self.pool.wait_settled('planner')  # the unused reserve is back in the pool
                    parent()
        except Exception:
            self.pool.stop(HARD_ABORT, 'TASK_SOURCE_AUTHORIZATION_CHANGED')
            status = 'STOPPED'
        finally:
            self.pool.parent_validate = parent
        session = bound.get('session')
        if session is not None and session.failure in _HARD_FAILURES:
            self.pool.stop(HARD_ABORT, session.failure)
        if session is not None and session.failure == 'PROVIDER_TIMEOUT_UNCERTAIN':
            found.pop('plan', None)
            found['reason'] = 'PLANNER_TIMEOUT'
        if status != 'RUNNING':
            found['reason'] = 'PLANNER_' + status
        if 'planner' not in self.pool.branches:
            self.pool.skip_aux('planner')
        if found.get('plan') and not self.pool.stop_state:
            self.subplans, self.planner = found['plan'], dict(source='LLM', reason=None)
        else:
            self.planner = dict(source='TEMPLATE' if self.subplans else 'NONE',
                                reason=found.get('reason') or 'PLANNER_STOPPED')
        return self.subplans

    def snapshot(self):
        try:
            self.pool.parent_validate()
        except Exception:
            self.pool.stop(HARD_ABORT, 'TASK_SOURCE_AUTHORIZATION_CHANGED')
        stop = self.pool.stop_state
        outcome = self.outcome
        withheld = stop and stop[0] in (HARD_ABORT, CANCELLED)
        status = 'running' if not withheld and outcome is None else 'failed'
        reason = stop[1] if withheld else 'PARALLEL_NO_VERIFIED_RESULT'
        snapshot = project_run_snapshot(query=self.query, workspace=self.workspace,
            status=RunStatus(run_id=self.run_id, thread_id=self.run_id,
                status=status,
                error=dict(code=reason, category='runtime', message='Parallel investigation stopped.')))
        if withheld:
            snapshot['parallel_investigation'] = dict(
                status='ABORTED' if stop[0] == HARD_ABORT else 'CANCELLED', reason=reason)
            if stop[0] == CANCELLED:
                snapshot.update(kind='cancelled', error=None)
                snapshot['run']['status'] = 'cancelled'
        elif outcome:
            results = outcome['results']
            snapshot['parallel_investigation'] = dict(status=outcome['status'],
                branches=[row['run'] for row in results.values()], gaps=outcome['gaps'],
                unstarted=outcome['unstarted'])
            if results:
                from linkloom.ui.branch_synthesis import merge_evidence, synthesize_reviewed
                merged = merge_evidence(results.values())
                snapshot.update(kind='success' if outcome['status'] == 'COMPLETE' else 'insufficient',
                                error=None)
                snapshot['run'].update(status='completed', stage='ready')
                snapshot['evidence'] = [deepcopy(item) for item in merged.values()]
                # The answer is composed once per outcome so repeated reads agree. Authorization was
                # re-validated above on every call, and a withheld snapshot never reaches this branch.
                if self._answer is None:
                    self._answer = synthesize_reviewed(
                        list(results.values()), outcome['gaps'], outcome['unstarted'],
                        merged, [q for q, _ in self.subplans],
                        source_check=self.pool.parent_validate, review_id=f'{self.run_id}-aggregate')
                snapshot['parallel_investigation']['answer'] = deepcopy(self._answer)
        else:
            snapshot['parallel_investigation'] = dict(status='RUNNING')
        if self.planner is not None:
            snapshot['parallel_investigation']['planner'] = dict(self.planner)
        snapshot['read_only'] = True
        return snapshot

    def execute(self):
        try:
            for number, (question, plan) in enumerate(self.subplans, 1):
                name = f'branch-{number}'
                self.pool.launch(name, lambda session, branch=name, q=question, p=plan:
                                 self._execute_branch(branch, q, p, session))
        except Exception:
            self.pool.stop(HARD_ABORT, 'PARALLEL_BRANCH_LAUNCH_FAILED')
        remaining = max(0, self.pool.total.max_seconds - (monotonic() - self.pool.started))
        try:
            outcome = self.pool.finish(timeout=remaining, stop_at_timeout=True)
        except (OSError, ValueError, KeyError, TypeError):
            self.pool.stop(HARD_ABORT, 'PARALLEL_RECEIPT_INVALID')
            return
        self.outcome = outcome

    def _execute_branch(self, name, question, plan, session):
        turns = 2 * len(plan.subgoals) + 1 if plan else 3
        if session.limits.max_requests < turns or session.limits.max_output_tokens < turns:
            raise ValueError('PARALLEL_BRANCH_BUDGET_INSUFFICIENT')
        adapter = _OutputBoundedAdapter(session.bind(self.model_factory(question), 'runtime'),
                                        min(4096, session.limits.max_output_tokens // turns))
        engine = RuntimeEngine(vault_root=self.vault, index_path=self.index,
            checkpoint_dir=session.root / 'runtime', trace_dir=session.root / 'traces',
            model=adapter, memory_enabled=False, decision_memory_enabled=False,
            decision_memory_workspace_id=self.workspace_id)
        status = engine.start_multi_agent(RunRequest(request_id=f'{self.run_id}-{name}',
            thread_id=f'{self.run_id}-{name}', query=question, workflow='team_decision',
            task_plan=plan.to_dict() if plan else None, planning_mode='template',
            max_steps=turns, max_provider_requests=turns, dry_run=True, caller='product_ui'))
        if status.status != 'completed' or not status.result_ref:
            raise ValueError('PARALLEL_RUNTIME_FAILED')
        artifact = json.loads((engine.checkpoint_dir / status.result_ref).read_text(encoding='utf-8'))
        review = project_reviewer(artifact)
        if review['decision'] != 'evidence_sufficient':
            raise ValueError('PARALLEL_REVIEW_FAILED')
        state = engine.checkpointer.load(status.thread_id)
        snapshot = project_run_snapshot(query=question, workspace=self.workspace, status=status,
            result_artifact=artifact, tool_ledger=state.tool_ledger)
        observed = collect_observed_evidence(state.tool_ledger)
        evidence = [dict(item, **self.source_versions[item['relative_path']],
                         source_name=self.source_names[item['relative_path']],
                         content_sha256=observed[item['ref']]['content_sha256'],
                         quote_sha256=observed[item['ref']]['quote_sha256'])
                    for item in snapshot['evidence']]
        if not evidence:
            raise ValueError('PARALLEL_EVIDENCE_MISSING')
        team = artifact['result']['team_decision']
        # The artifact carries no structured subject/relation. The key comes from the Host-validated plan:
        # the exact question of the branch's last subgoal (or the branch question for a single step).
        # Model text is never used to build it, so equal keys mean the same plan question was asked twice.
        asked = plan.subgoals[-1].question if plan else question
        conclusion = dict(question=question, decision=dict(value=team['decision']['value'],
                              status=team['decision']['status']),
                          unresolved_items=[item['description'] for item in team['unresolved_items']],
                          uncertainty=dict(status=team['uncertainty']['status'],
                                           statement=team['uncertainty']['statement']),
                          evidence=[{key: item[key] for key in ('source_id', 'source_version', 'line_start',
                                                                'line_end', 'quote_sha256')}
                                    for item in evidence],
                          claim_key=dict(subject=asked, relation='subgoal_question'),
                          claim_value=team['decision']['value'])
        return dict(run=dict(branch=name, run_id=status.run_id, thread_id=status.thread_id,
                             review_id=review['review_id']), evidence=evidence, conclusion=conclusion)

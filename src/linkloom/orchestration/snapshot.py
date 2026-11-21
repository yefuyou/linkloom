"""Product snapshot of an orchestration run, built only from the LangGraph state.

The same code renders a live run and a run read cold from the LangGraph checkpoint. Nothing here calls a model
or writes a file. The caller re-validates source authorization before every render and passes the result as
`withheld`; a withheld snapshot never carries evidence or an answer.
"""
from __future__ import annotations

from copy import deepcopy

from linkloom.orchestration import blackboard
from linkloom.orchestration.state import ABSTAINED, ACCEPTED, FAILED, PENDING, RUNNING, SKIPPED
from linkloom.orchestration.worker import UNCERTAIN_FAILURES
from linkloom.runtime.models import RunStatus
from linkloom.ui.branch_synthesis import merge_evidence
from linkloom.ui.projection import project_run_snapshot

ABORTED, CANCELLED = 'ABORTED', 'CANCELLED'
# What a viewer may do with a task that has not produced a verified result.
REUSED, CONTINUABLE, UNCERTAIN, WITHHELD = 'REUSED', 'CONTINUABLE', 'UNCERTAIN', 'WITHHELD'


def task_rows(values):
    rows = values.get('rows') or {}
    evidence = values.get('verified_evidence') or {}
    out = []
    for task_id, task in (values.get('tasks') or {}).items():
        item = dict(id=task_id, description=task['description'], depends_on=list(task['depends_on']),
                    status=task['status'], depth=task.get('depth'), round=task.get('round'),
                    spawn_reason=task.get('spawn_reason'), failure=task.get('failure'),
                    attempt=task.get('attempt'), result_ref=task.get('result_ref'))
        if task_id in rows:
            row = rows[task_id]
            item['verified_decision'] = row['conclusion']['decision']['value']
            item['review_id'] = row['run'].get('review_id')
            item['gaps'] = blackboard.gaps_of(row)
        item['evidence_keys'] = sorted(key for key, entry in evidence.items() if entry.get('task_id') == task_id)
        out.append(item)
    return out


def task_gaps(values):
    gaps = {}
    for task_id, task in (values.get('tasks') or {}).items():
        if task['status'] == FAILED:
            gaps[task_id] = 'UNCERTAIN' if task.get('failure') in UNCERTAIN_FAILURES else 'FAILED'
        elif task['status'] == SKIPPED:
            gaps[task_id] = 'SKIPPED'
    return gaps


def task_states(values, *, running):
    """Per task: what happened and whether the product may continue it (resume re-dispatches nothing paid)."""
    states = []
    for task_id, task in (values.get('tasks') or {}).items():
        status = task['status']
        if status in (ACCEPTED, ABSTAINED):
            states.append(dict(branch=task_id, state=REUSED, reason='VERIFIED'))
        elif status == FAILED and task.get('failure') in UNCERTAIN_FAILURES:
            states.append(dict(branch=task_id, state=UNCERTAIN, reason=task['failure']))
        elif status in (PENDING, RUNNING) and not running:
            states.append(dict(branch=task_id, state=CONTINUABLE, reason='NOT_FINISHED'))
        elif status == RUNNING:
            states.append(dict(branch=task_id, state=RUNNING, reason='IN_PROGRESS'))
        else:
            states.append(dict(branch=task_id, state=WITHHELD if status == SKIPPED else FAILED,
                               reason=task.get('failure') or status.upper()))
    return states


def blackboard_summary(values):
    return dict(raw_retrievals=len(values.get('raw_retrievals') or {}),
                verified_evidence=[dict(key=key, task_id=entry['task_id'], source_id=entry['source_id'],
                                        source_version=entry['source_version'], line_start=entry['line_start'],
                                        line_end=entry['line_end'], quote_sha256=entry['quote_sha256'],
                                        verification=entry['verification'])
                                   for key, entry in (values.get('verified_evidence') or {}).items()],
                inferences=len(values.get('inferences') or {}),
                approved_decisions=deepcopy(values.get('approved_decisions') or []))


def public_scope(values):
    """The confirmed scope without the Host's blocked-entity list (it names other workspaces)."""
    scope = {key: value for key, value in (values.get('confirmed_scope') or {}).items() if key != 'blocked_entities'}
    return dict(confirmed_scope=scope, entity_leads=list(values.get('entity_leads') or []))


def render_snapshot(run_id, query, workspace, values, *, running, withheld=None, pending=(), continuable=True):
    """withheld: None, (ABORTED|CANCELLED, reason). pending: interrupt payloads waiting for a human."""
    values = values or {}
    status = values.get('status') or 'RUNNING'
    if withheld is None and status == ABORTED:
        withheld = (ABORTED, values.get('abort_reason') or 'ORCHESTRATION_ABORTED')
    if withheld:
        kind, reason = withheld
        snapshot = project_run_snapshot(query=query, workspace=workspace, status=RunStatus(
            run_id=run_id, thread_id=run_id, status='failed',
            error=dict(code=reason, category='runtime', message='Orchestrated investigation stopped.')))
        snapshot['parallel_investigation'] = dict(status=kind, reason=reason, tasks=[], **public_scope(values))
        if kind == CANCELLED:
            snapshot.update(kind='cancelled', error=None)
            snapshot['run']['status'] = 'cancelled'
        snapshot['read_only'] = True
        return snapshot
    finished = status != 'RUNNING'
    snapshot = project_run_snapshot(query=query, workspace=workspace, status=RunStatus(
        run_id=run_id, thread_id=run_id, status='failed' if finished else 'running',
        error=dict(code='PARALLEL_NO_VERIFIED_RESULT', category='runtime',
                   message='Orchestrated investigation stopped.') if finished else None))
    tasks = values.get('tasks') or {}
    outcome = dict(status=status, tasks=task_rows(values), gaps=task_gaps(values),
                   unstarted=sum(1 for task in tasks.values() if task['status'] in (PENDING, SKIPPED)),
                   blackboard=blackboard_summary(values), rounds=values.get('round', 0),
                   dispatches=values.get('dispatches', 0), trace=deepcopy(values.get('trace') or []),
                   **public_scope(values))
    if pending:
        outcome['awaiting_confirmation'] = deepcopy(list(pending))
    rows = values.get('rows') or {}
    if finished:
        reviewed = [rows[task_id] for task_id in tasks if task_id in rows]
        outcome['branches'] = [row['run'] for row in reviewed]
        merged = merge_evidence(reviewed)
        answer = values.get('answer')
        worker_reviews = {task_id: deepcopy(row['run']['reviewer']) for task_id, row in rows.items()
                          if isinstance(row['run'].get('reviewer'), dict)}
        aggregate_review = deepcopy((answer or {}).get('reviewer') or dict(
            performed=None, decision=None, reason='REVIEW_RECORD_UNAVAILABLE'))
        outcome['reviews'] = dict(workers=worker_reviews, aggregate=aggregate_review,
                                 unavailable_worker_reviews=[task_id for task_id in tasks if task_id not in worker_reviews])
        snapshot['reviewer'] = aggregate_review
        snapshot.update(kind='success' if status == 'COMPLETE' else 'insufficient', error=None)
        snapshot['run'].update(status='completed', stage='ready')
        snapshot['evidence'] = [deepcopy(item) for item in merged.values()]
        if answer is not None:
            outcome['answer'] = deepcopy(answer)
            # Reported next to, never inside, `status`: completeness and the conflict check are separate facts.
            check = answer.get('conflict_check') or {}
            outcome['conflict_check_status'] = check.get('status', 'UNAVAILABLE')
            outcome['conflict_check_verified'] = check.get('verified') is True
    if not running:
        # A viewer of a saved run learns which tasks a resume would run; accepted tasks are reused, not repaid.
        outcome['branch_states'] = task_states(values, running=False)
        outcome['continuable'] = ([item['branch'] for item in outcome['branch_states']
                                   if item['state'] == CONTINUABLE] if continuable and not finished else [])
    snapshot['parallel_investigation'] = outcome
    snapshot['read_only'] = True
    return snapshot

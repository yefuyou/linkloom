"""Read-only call chain of an orchestration run: the task DAG, Supervisor decisions, waves and Provider receipts.

Everything comes from the LangGraph state and the Provider journals under the run directory. Task ids are
validated by the Supervisor contract, and every path is checked to stay inside the run directory.
"""
from __future__ import annotations

from pathlib import Path

from linkloom.orchestration.snapshot import blackboard_summary, public_scope, task_rows
from linkloom.ui.task_provider import journal_request_count, read_provider_journal

_KEYS = ('requests', 'input_tokens', 'output_tokens')


def _receipt(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        return None
    journal = read_provider_journal(path)
    if journal is None:
        return None
    rows = [row for row in journal.get('attempts', []) if isinstance(row, dict)]
    usage = [row.get('usage') if isinstance(row.get('usage'), dict) else {} for row in rows]
    return dict(requests=journal_request_count(journal),
                input_tokens=sum(item.get('input_tokens') or 0 for item in usage),
                output_tokens=sum(item.get('output_tokens') or 0 for item in usage),
                failure=journal.get('failure'),
                attempts=[dict(sequence=row.get('sequence'), stage=row.get('stage'), outcome=row.get('outcome'))
                          for row in rows])


def build_trace(root, run_id, values, outcome):
    root = Path(root)
    tasks = []
    totals = dict.fromkeys(_KEYS, 0)
    for task in task_rows(values):
        attempts = []
        base = root / 'tasks' / task['id']
        for directory in sorted(base.glob('attempt-*')) if base.is_dir() else ():
            receipt = _receipt(directory, root)
            if receipt is not None:
                attempts.append(dict(receipt, attempt=directory.name))
        tasks.append(dict(task, attempts=attempts))
    supervisor = []
    for directory in sorted((root / 'supervisor').glob('round-*')) if (root / 'supervisor').is_dir() else ():
        receipt = _receipt(directory, root)
        if receipt is not None:
            supervisor.append(dict(receipt, round=directory.name))
    comparison = _receipt(root / 'comparison', root)
    for item in (*supervisor, *([comparison] if comparison else ()), *(attempt for task in tasks for attempt in task['attempts'])):
        for key in _KEYS:
            totals[key] += item[key] or 0
    answer = values.get('answer') or {}
    return dict(run_id=run_id, status=outcome['status'], rounds=values.get('round', 0),
                dispatches=values.get('dispatches', 0), tasks=tasks, supervisor=supervisor,
                comparison=comparison, events=list(values.get('trace') or []), blackboard=blackboard_summary(values),
                synthesizer=dict(status=answer.get('status'), published=answer.get('published'),
                                 fallback_reason=answer.get('fallback_reason'),
                                 cross_branch_compared=(answer.get('conflict_check') or {}).get('cross_branch_compared'),
                                 conflict_check=(answer.get('conflict_check') or {}).get('status'),
                                 conflict_check_verified=(answer.get('conflict_check') or {}).get('verified')),
                totals=totals, **public_scope(values))

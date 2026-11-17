"""Read-only call chain of an orchestration run: the task DAG, Supervisor decisions, waves and Provider receipts.

Everything comes from the LangGraph state and the budget receipts under the run directory. Task ids are
validated by the Supervisor contract, and every path is checked to stay inside the run directory.
"""
from __future__ import annotations

from pathlib import Path

from linkloom.orchestration.files import read_json
from linkloom.orchestration.snapshot import blackboard_summary, public_scope, task_rows

_KEYS = ('requests_reserved', 'input_tokens_reserved', 'output_tokens_reserved', 'cost_reserved_usd')


def _receipt(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        return None
    budget = read_json(path / 'budget.json')
    if not isinstance(budget, dict):
        return None
    return dict({key: budget.get(key) for key in _KEYS}, failure=budget.get('failure'),
                attempts=[dict(sequence=row.get('sequence'), stage=row.get('stage'), outcome=row.get('outcome'))
                          for row in budget.get('attempts', [])])


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
    for item in (*supervisor, *(attempt for task in tasks for attempt in task['attempts'])):
        for key in _KEYS:
            totals[key] += item[key] or 0
    answer = values.get('answer') or {}
    return dict(run_id=run_id, status=outcome['status'], rounds=values.get('round', 0),
                dispatches=values.get('dispatches', 0), tasks=tasks, supervisor=supervisor,
                events=list(values.get('trace') or []), blackboard=blackboard_summary(values),
                synthesizer=dict(status=answer.get('status'), published=answer.get('published'),
                                 fallback_reason=answer.get('fallback_reason'),
                                 cross_branch_compared=(answer.get('conflict_check') or {}).get('cross_branch_compared')),
                totals=totals, **public_scope(values))

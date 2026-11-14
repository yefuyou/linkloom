"""Cold read of a durable parallel run and the list of branches that may still be continued.

Nothing here calls a model or writes into a run directory. Every branch is classified by the unchanged
classify_branch_for_resume: a finished branch is reused, a branch whose request outcome is unknown stays a
gap and is never resent, and only a branch that never sent a request can be continued.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from linkloom.runtime.models import RunStatus
from linkloom.runtime.task_plan import validate_model_proposed_plan
from linkloom.ui.branch_budget import CANCELLED, HARD_ABORT, classify_branch_for_resume
from linkloom.ui.parallel_investigation import build_branch_row
from linkloom.ui.projection import WorkspacePresentationContext

RESUME_DIR = 'resume'
REUSED, UNCERTAIN, CONTINUABLE, WITHHELD, RESUME_NOT_SAFE = (
    'REUSED', 'UNCERTAIN', 'CONTINUABLE', 'WITHHELD', 'RESUME_NOT_SAFE')


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def load_record(root):
    record = _json(Path(root) / 'parallel.json')
    if (record['schema'] != 'parallel-run/v1' or not isinstance(record['query'], str)
            or not isinstance(record['identity'], str) or not isinstance(record['branches'], list)
            or not all(isinstance(b['name'], str) and isinstance(b['question'], str) for b in record['branches'])):
        raise ValueError('PARALLEL_RECORD_INVALID')
    return record


def resumed_names(root, names):
    """Branch names already handed to a continuation. An unreadable marker counts every branch as handed over."""
    path = Path(root) / 'resumed.json'
    if not path.exists():
        return frozenset()
    try:
        listed = _json(path)['branches']
        if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
            raise ValueError('RESUMED_INVALID')
        return frozenset(listed)
    except Exception:
        return frozenset(names)


def claim_continuation(root, names):
    """Keep the exclusive marker even if its writer is interrupted."""
    with (Path(root) / 'resumed.json').open('x', encoding='utf8') as stream:
        json.dump(dict(branches=names), stream)
        stream.flush()
        os.fsync(stream.fileno())


def plan_of(entry, max_steps):
    if entry['plan'] is None:
        return None
    return validate_model_proposed_plan(entry['plan'], user_query=entry['plan']['goal'], max_steps=max_steps)


def _hard_stop(root):
    """The first HARD_ABORT/CANCELLED stop recorded by the original pool or the continuation pool."""
    found = None
    for pool in (Path(root) / 'pool.json', Path(root) / RESUME_DIR / 'pool.json'):
        try:
            stop = _json(pool)['stop']
        except Exception:
            if pool.parent.name == RESUME_DIR:
                continue  # no continuation, or its branches are classified UNCERTAIN below; nothing is resent
            raise
        if isinstance(stop, list) and len(stop) == 2 and stop[0] in (HARD_ABORT, CANCELLED):
            if found is None or stop[0] == HARD_ABORT:
                found = (stop[0], str(stop[1]))
    return found


def _row_of(root, name):
    row = _json(Path(root) / 'branches' / name / 'result.json')
    if (not isinstance(row, dict) or not isinstance(row.get('run'), dict) or row['run'].get('branch') != name
            or not isinstance(row.get('evidence'), list) or not isinstance(row.get('conclusion'), dict)):
        raise ValueError('PARALLEL_RESULT_INVALID')
    return row


def _rebuilt_row(base, name, entry, record, run_id, max_steps):
    """Read-only rebuild from a completed Runtime checkpoint of a branch that finished all its requests."""
    from linkloom.runtime.checkpoint import SQLiteCheckpointer
    runtime = Path(base) / 'branches' / name / 'runtime'
    checkpointer = SQLiteCheckpointer(runtime)
    state = checkpointer.get_latest(f'{run_id}-{name}')
    if state is None or state.status != 'completed' or not state.result_ref:
        raise ValueError('RESUME_NOT_COMPLETE')
    status = RunStatus(state.run_id, state.thread_id, state.status, result_ref=state.result_ref)
    workspace = WorkspacePresentationContext(display_name=record['workspace']['display_name'],
                                             document_count=record['workspace']['document_count'])
    return build_branch_row(name, entry['question'], plan_of(entry, max_steps), status, runtime, checkpointer,
                            workspace, record['source_versions'], record['source_names'])


def classify_run(root, record, run_id, max_steps):
    """Return dict(stop, results, gaps, states). A hard stop withholds every branch."""
    root = Path(root)
    names = [entry['name'] for entry in record['branches']]
    stop = _hard_stop(root)
    if stop:
        return dict(stop=stop, results={}, gaps={}, states=[
            dict(branch=name, state=WITHHELD, reason=stop[1]) for name in names])
    resumed = resumed_names(root, names)
    results, gaps, states = {}, {}, []

    def state(name, kind, reason, message=None):
        states.append(dict(branch=name, state=kind, reason=reason,
                           **({} if message is None else dict(message=message))))

    for entry in record['branches']:
        name = entry['name']
        again = name in resumed
        base = root / RESUME_DIR if again else root
        kind = classify_branch_for_resume(base / 'branches' / name)
        if kind == 'WITHHELD':
            return dict(stop=(HARD_ABORT, 'PARALLEL_BRANCH_WITHHELD'), results={}, gaps={}, states=[
                dict(branch=n, state=WITHHELD, reason='PARALLEL_BRANCH_WITHHELD') for n in names])
        if kind == 'REUSE':
            try:
                results[name] = _row_of(base, name)
                state(name, REUSED, 'RESULT_VERIFIED')
            except Exception:
                gaps[name] = 'UNCERTAIN'
                state(name, UNCERTAIN, 'RESULT_INVALID')
        elif kind == 'RESUME':
            try:
                results[name] = _rebuilt_row(base, name, entry, record, run_id, max_steps)
                state(name, REUSED, 'REBUILT_FROM_CHECKPOINT')
            except Exception:
                gaps[name] = 'RESUME_NOT_SAFE'
                state(name, RESUME_NOT_SAFE, 'CHECKPOINT_NOT_REUSABLE')
        elif kind == 'START' and not again:
            gaps[name] = 'NOT_STARTED'
            state(name, CONTINUABLE, 'BRANCH_NOT_STARTED')
        elif kind == 'START':
            gaps[name] = 'RESUME_NOT_SAFE'
            state(name, RESUME_NOT_SAFE, 'BRANCH_ALREADY_RESUMED')
        else:
            gaps[name] = 'UNCERTAIN'
            state(name, UNCERTAIN, 'REQUEST_OUTCOME_UNCERTAIN',
                  'The request outcome is uncertain and may have been billed. It is never resent.')
    return dict(stop=None, results=results, gaps=gaps, states=states)


def outcome_of(classified):
    results, gaps = classified['results'], classified['gaps']
    status = 'COMPLETE' if results and not gaps else 'PARTIAL' if results else 'FAILED'
    return dict(status=status, reason=None, results=results, gaps=gaps, unstarted=0)

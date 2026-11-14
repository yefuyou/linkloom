"""Budget pool for parallel branches, one unchanged TaskProviderSession per branch.

A single session is single-flight by design, so parallel branches each own a
child session with a fixed slice of one shared budget. Nothing here is wired
into a product path. Stop semantics:
HARD_ABORT (authorization/workspace change, 401/402/403): publish nothing.
SOFT_STOP (the pool deadline, or an explicit stop): stop new requests, verified
branches publish as PARTIAL. A branch exhausting its own slice does not trigger it:
slices sum to at most the total, so that branch simply fails alone.
CANCELLED (user): stop new requests, publish nothing.
A branch that is UNCERTAIN or times out only stops itself and leaves a gap.
If finish() times out with a branch still running, that branch is never published;
a late writer is marked DISCARDED, and nothing is released.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import re
from threading import Event, Lock, RLock
from time import monotonic

from linkloom.ui.lifecycle import BackgroundShutdownTimeout, BackgroundTasks, shutdown_deadline
from linkloom.ui.task_provider import (
    TaskProviderSession, pass_router_reservation, remaining_provider_limits, validate_task_provider_receipt)

HARD_ABORT, SOFT_STOP, CANCELLED = 'HARD_ABORT', 'SOFT_STOP', 'CANCELLED'
_RANK = {SOFT_STOP: 1, CANCELLED: 2, HARD_ABORT: 3}
_HARD_FAILURES = frozenset({'MODEL_AUTH_REQUIRED', 'MODEL_BILLING_BLOCKED'})
_KEYS = ('max_requests', 'max_input_tokens', 'max_output_tokens', 'max_cost_usd')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')


def _write_json(path, data):
    """Atomic replace: an interrupted write leaves the previous file intact."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def classify_branch_for_resume(branch_dir):
    """Classify a child using its allocation, stop state, and durable receipt."""
    root = Path(branch_dir)
    try:
        if root.parent.name != 'branches':
            return 'UNCERTAIN'
        journal = json.loads((root.parent.parent / 'pool.json').read_text(encoding='utf8'))
        allocation = journal['branches'][root.name]
        stop = journal['stop']
        if stop is not None:
            if (not isinstance(stop, list) or len(stop) != 2 or stop[0] not in _RANK
                    or not isinstance(stop[1], str)):
                return 'UNCERTAIN'
            if stop[0] in (HARD_ABORT, CANCELLED):
                return 'WITHHELD'
        budget = json.loads((root / 'budget.json').read_text(encoding='utf8'))
        attempts = budget['attempts']
        if (allocation['status'] not in ('RUNNING', 'VERIFIED')
                or allocation['slice'] != {key: budget['limits'][key] for key in _KEYS}
                or any(not 0 < allocation['slice'][key] <= journal['total'][key] for key in _KEYS)
                or not isinstance(attempts, list) or budget['failure'] is not None
                or budget['requests_reserved'] != len(attempts)):
            return 'UNCERTAIN'
        outcomes = [row['outcome'] for row in attempts]
        if not attempts:
            return ('START' if stop is None and allocation['status'] == 'RUNNING'
                    and not (root / 'result.json').exists()
                    and not any(budget[key] for key in ('input_tokens_reserved',
                                                        'output_tokens_reserved', 'cost_reserved_usd'))
                    else 'UNCERTAIN')
        if any(outcome != 'SUCCEEDED' for outcome in outcomes):
            return 'UNCERTAIN'  # PENDING may have crossed the transport; never auto-resend
        started = datetime.fromisoformat(budget['started_at'])
        completed = datetime.fromtimestamp(started.timestamp() + budget['elapsed_seconds'], UTC)
        validate_task_provider_receipt(root / 'budget.json', completed.isoformat())
        if (root / 'result.json').is_file():
            return 'REUSE' if allocation['status'] == 'VERIFIED' else 'UNCERTAIN'
        return 'RESUME' if stop is None and allocation['status'] == 'RUNNING' else 'UNCERTAIN'
    except Exception:
        return 'UNCERTAIN'


def validate_pool_receipt(root, completed_at=None):
    """Map branch name to None (receipt valid) or an error string."""
    completed_at = completed_at or datetime.now(UTC).isoformat()
    verdicts = {}
    for name in json.loads((Path(root) / 'pool.json').read_text(encoding='utf8'))['branches']:
        try:
            validate_task_provider_receipt(Path(root) / 'branches' / name / 'budget.json', completed_at)
            verdicts[name] = None
        except Exception as exc:
            verdicts[name] = str(exc) or type(exc).__name__
    return verdicts


class BranchBudgetPool:
    def __init__(self, root, limits, validate, run_id, branches, aux=()):
        if type(branches) is not int or branches < 1:
            raise ValueError('positive branch count required')
        self.root, self.run_id, self.parent_validate, self.declared = Path(root), run_id, validate, branches
        self.aux = frozenset(aux)  # declared slots (the Planner) that bill the pool but never publish
        self.total = remaining_provider_limits(limits)  # router spend is deducted exactly once, here
        self.share = {key: getattr(self.total, key) // branches if key in _KEYS[:3]
                      else getattr(self.total, key) / branches for key in _KEYS}
        self.available = {key: getattr(self.total, key) for key in _KEYS}
        self.started = monotonic()
        self.branches, self.stop_state, self.sealed = {}, None, False
        self._lock, self._validate_lock = RLock(), Lock()
        self._tasks = BackgroundTasks()
        self.root.mkdir(parents=True, exist_ok=True)
        self._save()

    def stop(self, kind, reason):
        with self._lock:
            if self.stop_state is None or _RANK[kind] > _RANK[self.stop_state[0]]:
                self.stop_state = (kind, reason)
                self._save()

    def _validated(self):
        with self._validate_lock:  # the parent validate is never called concurrently
            if self.stop_state:
                raise RuntimeError(self.stop_state[1])
            if monotonic() - self.started >= self.total.max_seconds:
                self.stop(SOFT_STOP, 'DEADLINE')
                raise RuntimeError('DEADLINE')
            try:
                self.parent_validate()
            except Exception:
                self.stop(HARD_ABORT, 'TASK_SOURCE_AUTHORIZATION_CHANGED')
                raise

    def pending_task_ids(self):
        with self._lock:
            sessions = [b['session'] for b in self.branches.values() if b['session'] is not None]
        return (*self._tasks.pending_ids(), *(task for session in sessions for task in session.pending_task_ids()))

    def close(self, timeout=10.0, *, deadline=None):
        deadline = shutdown_deadline(timeout) if deadline is None else deadline
        self._tasks.stop_accepting()
        self._tasks.wait(deadline)
        for branch in self.branches.values():
            if branch['session'] is not None:
                branch['session'].close(deadline=deadline)

    def skip_aux(self, name):
        """The declared auxiliary slot will not run: its share goes back to the real branches."""
        with self._lock:
            if name in self.aux and name not in self.branches:
                self.aux = self.aux - {name}
                self.declared -= 1

    def wait_settled(self, name, timeout=None):
        """Block until a launched branch thread has recorded its outcome and returned any unused slice."""
        with self._lock:
            branch = self.branches.get(name)
        return branch is None or branch['settled'].wait(timeout)

    def _share(self, name):
        if not self.aux or name in self.aux:
            return self.share
        # With a Planner the pool is shared again once it is done: split what is really available
        # among the branches that are still to start, so an unused Planner reserve is not stranded.
        left = max(1, self.declared - len(self.branches))
        return {key: self.available[key] // left if key in _KEYS[:3] else self.available[key] / left
                for key in _KEYS}

    def _slice(self, name, max_seconds=None):
        seconds = self.total.max_seconds - (monotonic() - self.started)
        if max_seconds is not None:
            seconds = min(seconds, max_seconds)
        share = self._share(name)
        cut = {key: min(share[key], self.available[key]) for key in _KEYS}
        if (seconds <= 0 or min(cut[key] for key in _KEYS[:3]) < 1
                or cut['max_cost_usd'] < self.total.request_cost_upper_bound_usd):
            return None  # would violate TaskProviderLimits; never build a zero-budget session
        return replace(self.total, **cut, max_seconds=seconds,
                       request_timeout_seconds=min(self.total.request_timeout_seconds, seconds))

    def launch(self, name, work, *, max_seconds=None):
        """Run work(session) on its own thread and session; returns the branch status.

        max_seconds optionally caps this child's lifetime below the remaining pool time."""
        if not _NAME.fullmatch(name):
            raise ValueError('invalid branch name')
        with self._lock:
            if name in self.branches:
                raise ValueError('duplicate branch: ' + name)
            if len(self.branches) >= self.declared:
                raise ValueError('more branches than declared')  # no slice, no journal entry
            if self.stop_state:
                return 'NOT_STARTED'
            limits = self._slice(name, max_seconds)
            branch = self.branches[name] = dict(status='INSUFFICIENT_BUDGET' if limits is None else 'RUNNING',
                slice=None if limits is None else {key: getattr(limits, key) for key in _KEYS},
                held=None, result=None, session=None, settled=Event())
            if limits is None:
                self._save()
                return branch['status']
            for key in _KEYS:
                self.available[key] -= branch['slice'][key]
            branch['held'] = dict(branch['slice'])
            try:
                self._save()  # the allocation is durable before the child session exists
                with pass_router_reservation(None):  # a child's limits must equal its slice
                    branch['session'] = TaskProviderSession(self.root / 'branches' / name, limits, self._validated)
                self._tasks.start(f'{self.run_id}-{name}', lambda: self._run(name, work))
            except BaseException as error:  # roll back: nothing ran, so the slice is free again
                for key in _KEYS:
                    self.available[key] += branch['slice'][key]
                del self.branches[name]
                if branch['session'] is not None:
                    try:
                        branch['session'].close(1.0)
                    except Exception as exc:
                        error.add_note(f'session close failed during rollback: {exc!r}')
                try:
                    self._save()
                except Exception as exc:  # never replace the original failure
                    error.add_note(f'pool.json rewrite failed during rollback: {exc!r}')
                raise
        return 'RUNNING'

    def _run(self, name, work):
        try:
            self._run_branch(name, work)
        finally:
            self.branches[name]['settled'].set()

    def _run_branch(self, name, work):
        branch = self.branches[name]
        try:
            result, failed = work(branch['session']), False
        except Exception:
            result, failed = None, True
        session = branch['session']
        try:
            session.close(1.0)
            closed = True
        except BackgroundShutdownTimeout:
            closed = False
        with self._lock:
            if session.failure in _HARD_FAILURES:
                self.stop(HARD_ABORT, session.failure)
            if self.stop_state or self.sealed:
                branch['status'] = 'DISCARDED'  # late results never publish and never reach the provider
            else:
                outcomes = [row['outcome'] for row in session.attempts]
                clean = (closed and not failed and session.failure is None and bool(outcomes)
                         and all(o == 'SUCCEEDED' for o in outcomes))
                if clean:
                    try:
                        _write_json(session.root / 'result.json', result)
                    except Exception:
                        clean = False
                if clean:
                    branch['status'], branch['result'] = 'VERIFIED', result
                    used = dict(max_requests=session.requests, max_input_tokens=session.input_tokens,
                                max_output_tokens=session.output_tokens, max_cost_usd=session.cost)
                    for key in _KEYS:  # only a clean, closed child returns its unused slice
                        self.available[key] += branch['slice'][key] - used[key]
                        branch['held'][key] = used[key]
                else:
                    pending = any(o in ('PENDING', 'UNCERTAIN') for o in outcomes)
                    branch['status'] = ('UNCERTAIN' if pending or not closed else
                                        'NO_REQUESTS' if not failed and session.failure is None and not outcomes else 'FAILED')
            self._save()

    def accounting(self):
        """Reservation ceiling (throttling) and actual spend (successful usage) are kept apart."""
        with self._lock:
            reserved = spent = 0.0
            for branch in self.branches.values():
                session = branch['session']
                if session is None:
                    continue
                reserved += session.cost
                for row in list(session.attempts):
                    if row['outcome'] == 'SUCCEEDED':
                        usage = row['usage']
                        spent += ((usage.get('input_tokens') or 0) * session.limits.input_usd_per_million
                                  + (usage.get('output_tokens') or 0) * session.limits.output_usd_per_million) / 1e6
            held = {key: sum(b['held'][key] for b in self.branches.values() if b['held']) for key in _KEYS}
            return dict(reserved_usd=reserved, spent_usd=spent, available=dict(self.available), held=held)

    def finish(self, timeout=10.0, *, stop_at_timeout=False):
        deadline = shutdown_deadline(timeout)
        self._tasks.stop_accepting()
        try:
            self._tasks.wait(deadline)
        except BackgroundShutdownTimeout:
            if stop_at_timeout:
                self.stop(SOFT_STOP, 'DEADLINE')
        for branch in list(self.branches.values()):
            if branch['session'] is not None:
                try:
                    branch['session'].close(0.0, deadline=deadline)
                except BackgroundShutdownTimeout:
                    pass
        with self._lock:
            self.sealed = True  # a writer finishing after this point is discarded, never published
            kind = self.stop_state[0] if self.stop_state else None
            published = {}
            if kind in (None, SOFT_STOP):
                receipts = validate_pool_receipt(self.root)
                published = {name: b['result'] for name, b in self.branches.items()
                             if name not in self.aux and b['status'] == 'VERIFIED'
                             and receipts.get(name, 'missing') is None}
            gaps = {name: b['status'] for name, b in self.branches.items()
                    if name not in published and name not in self.aux}
            unstarted = self.declared - len(self.aux) - len(gaps) - len(published)
            status = {HARD_ABORT: 'ABORTED', CANCELLED: 'CANCELLED'}.get(kind) or (
                'COMPLETE' if published and not gaps and unstarted == 0 else 'PARTIAL' if published else 'FAILED')
            return dict(status=status, reason=self.stop_state and self.stop_state[1], results=published,
                        gaps=gaps, unstarted=unstarted, **self.accounting())

    def _save(self):
        _write_json(self.root / 'pool.json', dict(run_id=self.run_id, total=asdict(self.total),
            stop=self.stop_state, branches={name: dict(status=b['status'], slice=b['slice'], held=b['held'])
                                            for name, b in self.branches.items()}))

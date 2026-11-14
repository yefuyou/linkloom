"""Offline tests for the branch budget pool. Barriers (with timeouts) prove
concurrency; no assertion depends on elapsed time."""
from dataclasses import replace
import json
import random
from threading import Barrier, BrokenBarrierError, Event, Lock, Thread
from time import perf_counter

import pytest

from linkloom.agents.model_adapter import (
    ModelAction, ModelGenerationOptions, ModelProviderError, ModelResponse, ModelTurnRequest, ModelUsage)
from linkloom.tools.contracts import ToolCall
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.ui import branch_budget
from linkloom.ui.branch_budget import (
    CANCELLED, HARD_ABORT, SOFT_STOP, BranchBudgetPool, classify_branch_for_resume, validate_pool_receipt)
from linkloom.ui.lifecycle import shutdown_deadline
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession, pass_router_reservation

NAMES = ('a', 'b', 'c')


def limits(**changes):
    values = dict(max_requests=6, max_input_tokens=60000, max_output_tokens=6000, max_cost_usd=.6,
                  max_seconds=30, request_timeout_seconds=5, request_cost_upper_bound_usd=.1)
    return TaskProviderLimits(**{**values, **changes})


def request(run_id='r'):
    return ModelTurnRequest(run_id=run_id, turn_id='turn-1', task_id='task', agent_id='agent',
        sequence=1, user_input='Synthetic source', observation=None, available_tools=[],
        model_id='gemini-3.8-flash', generation_options=ModelGenerationOptions(max_output_tokens=500))


def ok(text='ok'):
    return ModelResponse(action=ModelAction.final(text), usage=ModelUsage(input_tokens=10, output_tokens=4))


def timeout_response():
    return ModelResponse(error=ModelProviderError('MODEL_TIMEOUT', 'timeout', 'Timed out.', False,
                                                  outcome='known_failure'))


class Adapter:
    def __init__(self, fn=ok):
        self.fn, self.calls = fn, 0

    def complete(self, req):
        self.calls += 1
        return self.fn()


def ask(adapter, answer='ok'):
    def work(session):
        response = session.bind(adapter, 'inv').complete(request())
        if response.error:
            raise RuntimeError(response.error.code)
        return answer
    return work


def pool_for(tmp_path, validate=lambda: None, branches=3, **changes):
    return BranchBudgetPool(tmp_path / 'pool', limits(**changes), validate, 'run1', branches)


def settle(pool):
    pool._tasks.wait(shutdown_deadline(10))


def test_continuation_subtracts_actual_usage_and_uncertain_reservations(tmp_path):
    pool = BranchBudgetPool(tmp_path, limits(max_requests=12, max_cost_usd=1.2),
        lambda: None, 'run1', 3, aux={'planner': dict(max_requests=1, max_input_tokens=5000,
                                                   max_output_tokens=500, max_cost_usd=.1)})
    pool.launch('planner', ask(Adapter()))
    assert pool.wait_settled('planner', 5)
    pool.launch('uncertain', ask(Adapter(timeout_response)))
    pool.launch('never_sent', lambda _session: None)
    pool.finish()
    budget = json.loads((tmp_path / 'branches' / 'uncertain' / 'budget.json').read_text())
    assert budget['attempts'][0]['outcome'] == 'UNCERTAIN'
    resumed = branch_budget.continuation_limits(tmp_path, 'run1', limits(
        max_requests=20, max_cost_usd=2, max_input_tokens=100000, max_output_tokens=10000))
    assert resumed.max_requests == 10
    assert resumed.max_input_tokens == 60000 - 10 - budget['input_tokens_reserved']
    assert resumed.max_output_tokens == 6000 - 4 - budget['output_tokens_reserved']
    assert resumed.max_cost_usd == pytest.approx(1.2 - .1 - .00003)


def test_branches_issue_overlapping_provider_requests(tmp_path):
    barrier, spans, lock = Barrier(3, timeout=2), [], Lock()

    def overlapping():
        start = perf_counter()
        barrier.wait()  # BrokenBarrierError here means the requests were serialized
        with lock:
            spans.append((start, perf_counter()))
        return ok()
    pool = pool_for(tmp_path)
    for name in NAMES:
        assert pool.launch(name, ask(Adapter(overlapping), name)) == 'RUNNING'
    outcome = pool.finish()
    assert outcome['status'] == 'COMPLETE' and outcome['results'] == {n: n for n in NAMES}
    # Overlap is proven by the Barrier above (serialized requests would time out).
    # The timestamps are trace data only; coarse clocks (about 15.6 ms on Windows) can
    # make a start and an end equal, so the comparison is non-strict.
    assert len(spans) == 3 and max(s for s, _ in spans) <= min(e for _, e in spans)


def test_single_session_is_single_flight(tmp_path):
    barrier, broken = Barrier(3, timeout=.3), []

    def wait():
        try:
            barrier.wait()
        except BrokenBarrierError:
            broken.append(1)
            raise
        return ok()
    session = TaskProviderSession(tmp_path, limits(), lambda: None)
    bound = session.bind(Adapter(wait), 'inv')
    threads = [Thread(target=bound.complete, args=(request(),)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert broken and session.failure


def test_uncertain_branch_is_isolated_and_keeps_its_slice(tmp_path):
    pool = pool_for(tmp_path)
    pool.launch('a', ask(Adapter(), 'a'))
    pool.launch('b', ask(Adapter(timeout_response), 'b'))
    pool.launch('c', ask(Adapter(), 'c'))
    outcome = pool.finish()
    assert outcome['status'] == 'PARTIAL' and outcome['results'] == {'a': 'a', 'c': 'c'}
    assert outcome['gaps'] == {'b': 'UNCERTAIN'}
    assert pool.branches['b']['held'] == pool.branches['b']['slice']
    assert pool.available['max_requests'] == 2  # only a and c returned one unused request each
    assert validate_pool_receipt(tmp_path / 'pool')['b'] is not None


def blocked_scenario(tmp_path, validate=lambda: None, requests=1, branches=3):
    pool = pool_for(tmp_path, validate, branches)
    pool.launch('a', ask(Adapter(), 'a'))
    pool.launch('b', ask(Adapter(), 'b'))
    settle(pool)
    entered, release = Event(), Event()

    def hold():
        entered.set()
        release.wait(5)
        return ok()
    late = Adapter(hold)
    def work(session):
        bound = session.bind(late, 'inv')
        for _ in range(requests):
            response = bound.complete(request())
        if response.error:
            raise RuntimeError(response.error.code)
        return 'c'
    pool.launch('c', work)
    assert entered.wait(5)
    return pool, release, late


@pytest.mark.parametrize('kind,status,published', [
    (HARD_ABORT, 'ABORTED', {}), (SOFT_STOP, 'PARTIAL', {'a': 'a', 'b': 'b'}), (CANCELLED, 'CANCELLED', {})])
def test_stop_semantics(tmp_path, kind, status, published):
    pool, release, _ = blocked_scenario(tmp_path)
    pool.stop(kind, 'test')
    release.set()
    outcome = pool.finish()
    assert outcome['status'] == status and outcome['results'] == published
    assert outcome['gaps']['c'] == 'DISCARDED'


def test_authorization_revocation_publishes_nothing(tmp_path):
    revoked = []

    def validate():
        if revoked:
            raise ValueError('SOURCE_EVIDENCE_NOT_CURRENT')
    pool, release, _ = blocked_scenario(tmp_path, validate)
    revoked.append(1)
    release.set()
    outcome = pool.finish()
    assert outcome['status'] == 'ABORTED' and outcome['results'] == {}
    assert outcome['reason'] == 'TASK_SOURCE_AUTHORIZATION_CHANGED'


def test_auth_error_is_hard_abort(tmp_path):
    denied = ModelResponse(error=ModelProviderError('MODEL_AUTH_REQUIRED', 'authentication', 'No.', False))
    pool = pool_for(tmp_path)
    pool.launch('a', ask(Adapter(), 'a'))
    settle(pool)
    pool.launch('b', ask(Adapter(lambda: denied), 'b'))
    outcome = pool.finish()
    assert outcome['status'] == 'ABORTED' and outcome['results'] == {}


def test_late_result_is_dropped_and_sends_no_new_request(tmp_path):
    pool, release, late = blocked_scenario(tmp_path, requests=2, branches=4)
    pool.stop(SOFT_STOP, 'budget')
    assert pool.launch('d', ask(late)) == 'NOT_STARTED' and 'd' not in pool.branches
    release.set()
    outcome = pool.finish()
    assert late.calls == 1 and 'c' not in outcome['results'] and outcome['gaps'] == {'c': 'DISCARDED'}


def test_slices_never_exceed_total_and_reclaim_is_bounded(tmp_path):
    rng = random.Random(7)
    for round_ in range(12):
        count = rng.randint(1, 5)
        pool = BranchBudgetPool(tmp_path / str(round_), limits(), lambda: None, 'run1', count)
        for index in range(count):
            fn = ok if rng.random() < .6 else timeout_response
            pool.launch(f'b{index}', ask(Adapter(fn)))
            settle(pool)
            books = pool.accounting()
            for key in branch_budget._KEYS:
                assert books['available'][key] >= -1e-9
                assert books['held'][key] + books['available'][key] <= getattr(pool.total, key) + 1e-9
        for branch in pool.branches.values():
            for key in branch_budget._KEYS:
                assert branch['held'][key] <= branch['slice'][key] + 1e-9
        pool.finish()


def test_router_spend_is_deducted_once(tmp_path):
    router = TaskProviderSession(tmp_path / 'router', limits(), lambda: None)
    assert router.bind(Adapter(), 'route').complete(request()).action
    with pass_router_reservation(router):
        pool = pool_for(tmp_path, branches=2)
        pool.launch('a', ask(Adapter()))
        pool.launch('b', ask(Adapter()))
    assert pool.total.max_requests == 5 and pool.total.max_cost_usd == pytest.approx(.5)
    for name in 'ab':
        child = pool.branches[name]
        assert {key: getattr(child['session'].limits, key) for key in branch_budget._KEYS} == child['slice']
        assert child['slice']['max_requests'] == 2 and child['slice']['max_cost_usd'] == pytest.approx(.25)
    pool.finish()


@pytest.mark.parametrize('changes', [dict(max_requests=1), dict(max_cost_usd=.15), dict(max_output_tokens=1)])
def test_insufficient_slice_is_refused_without_a_session(tmp_path, changes):
    pool = pool_for(tmp_path, branches=2, **changes)
    assert pool.launch('a', ask(Adapter())) == 'INSUFFICIENT_BUDGET'
    assert pool.branches['a']['session'] is None and not (tmp_path / 'pool' / 'branches' / 'a').exists()
    assert pool.finish()['gaps'] == {'a': 'INSUFFICIENT_BUDGET'}


def run_branch(tmp_path, adapter):
    TaskProviderSession(tmp_path, limits(), lambda: None).bind(adapter, 'inv').complete(request())


def resume(directory, adapter):
    verdict = classify_branch_for_resume(directory)
    if verdict == 'START':
        adapter.complete(request())
    return verdict


def test_resume_classification(tmp_path):
    adapter = Adapter()
    root = tmp_path / 'pool'
    empty = root / 'branches' / 'empty'
    TaskProviderSession(empty, limits(), lambda: None)
    allocation = dict(status='RUNNING', slice={key: getattr(limits(), key) for key in branch_budget._KEYS})
    journal = dict(stop=None, total=allocation['slice'], branches={'empty': allocation, 'done': dict(allocation)})
    (root / 'pool.json').write_text(json.dumps(journal))
    assert resume(empty, adapter) == 'START' and adapter.calls == 1
    done = root / 'branches' / 'done'
    run_branch(done, Adapter())
    assert classify_branch_for_resume(done) == 'RESUME'
    journal['branches']['done']['status'] = 'VERIFIED'
    (root / 'pool.json').write_text(json.dumps(journal))
    (done / 'result.json').write_text('"ok"')
    assert classify_branch_for_resume(done) == 'REUSE'
    (done / 'result.json').unlink()
    budget = json.loads((done / 'budget.json').read_text())
    budget['attempts'][0]['outcome'] = 'PENDING'
    (done / 'budget.json').write_text(json.dumps(budget))
    (tmp_path / 'broken').mkdir()
    (tmp_path / 'broken' / 'budget.json').write_text('{')
    adapter.calls = 0
    for directory in (done, tmp_path / 'broken', tmp_path / 'missing'):
        assert resume(directory, adapter) == 'UNCERTAIN'
    uncertain = tmp_path / 'uncertain'
    run_branch(uncertain, Adapter(timeout_response))
    assert resume(uncertain, adapter) == 'UNCERTAIN' and adapter.calls == 0
    (tmp_path / 'broken' / 'budget.json').write_text(json.dumps({'attempts': [{}]}))
    assert classify_branch_for_resume(tmp_path / 'broken') == 'UNCERTAIN'


@pytest.mark.parametrize('kind', [HARD_ABORT, CANCELLED])
@pytest.mark.parametrize('attempted', [False, True])
def test_global_stop_precedes_start_and_resume(tmp_path, kind, attempted):
    pool = pool_for(tmp_path, branches=2)
    ready, release = Event(), Event()

    def paused(session):
        if attempted:
            ask(Adapter())(session)
        ready.set()
        assert release.wait(5)

    pool.launch('a', paused)
    try:
        assert ready.wait(5)
        branch = pool.root / 'branches' / 'a'
        assert classify_branch_for_resume(branch) == ('RESUME' if attempted else 'START')
        pool.stop(kind, 'test stop')
        assert classify_branch_for_resume(branch) == 'WITHHELD'
    finally:
        release.set()
        pool.finish()


def test_missing_parent_or_inconsistent_slice_is_uncertain(tmp_path):
    root, branch = pooled_branch(tmp_path)
    journal_path = root / 'pool.json'
    journal = json.loads(journal_path.read_text())
    journal['branches']['a']['slice']['max_requests'] += 1
    journal_path.write_text(json.dumps(journal))
    assert classify_branch_for_resume(branch) == 'UNCERTAIN'
    journal_path.rename(root / 'pool.saved.json')
    assert classify_branch_for_resume(branch) == 'UNCERTAIN'


def test_each_branch_owns_its_ledger(tmp_path):
    def work(name):
        def run(session):
            ledger = ToolExecutionLedger()
            ledger.record_pending(ToolCall(call_id=f'{name}-call', tool_id='search', arguments={}, run_id='r'))
            session.bind(Adapter(), 'inv').complete(request())
            return [record.call_id for record in ledger.to_list()]
        return run
    pool = pool_for(tmp_path)
    for name in NAMES:
        pool.launch(name, work(name))
    assert pool.finish()['results'] == {name: [f'{name}-call'] for name in NAMES}


def test_parent_validate_is_never_concurrent(tmp_path):
    guard, overlaps, barrier = Lock(), [], Barrier(3, timeout=2)

    def validate():
        if not guard.acquire(blocking=False):
            overlaps.append(1)
            return
        try:
            Event().wait(.005)
        finally:
            guard.release()

    def meet():
        barrier.wait()
        return ok()
    pool = pool_for(tmp_path, validate)
    for name in NAMES:
        pool.launch(name, ask(Adapter(meet), name))
    assert pool.finish()['status'] == 'COMPLETE' and not overlaps


def test_pool_journal_survives_interrupted_write(tmp_path, monkeypatch):
    pool = pool_for(tmp_path)
    path = tmp_path / 'pool' / 'pool.json'
    before = json.loads(path.read_text())

    def interrupted(src, dst):
        raise OSError('simulated crash')
    monkeypatch.setattr(branch_budget.os, 'replace', interrupted)
    with pytest.raises(OSError):
        pool.stop(SOFT_STOP, 'x')
    assert json.loads(path.read_text()) == before


def tamper(path, **changes):
    budget = json.loads(path.read_text())
    budget.update(changes)
    path.write_text(json.dumps(budget))


def test_receipt_gate_blocks_publication_and_resume_reuse(tmp_path):
    pool = pool_for(tmp_path, branches=2)
    pool.launch('a', ask(Adapter(), 'a'))
    pool.launch('b', ask(Adapter(), 'b'))
    settle(pool)
    assert pool.branches['b']['status'] == 'VERIFIED'
    branch = tmp_path / 'pool' / 'branches' / 'b'
    assert classify_branch_for_resume(branch) == 'REUSE'
    tamper(branch / 'budget.json', input_tokens_reserved=1)  # every attempt still reads SUCCEEDED
    assert classify_branch_for_resume(branch) == 'UNCERTAIN'
    outcome = pool.finish()
    assert outcome['status'] == 'PARTIAL' and outcome['results'] == {'a': 'a'}
    assert outcome['gaps'] == {'b': 'VERIFIED'}


def test_branch_without_requests_is_not_verified_and_keeps_its_slice(tmp_path):
    pool = pool_for(tmp_path, branches=2)
    pool.launch('a', lambda session: 'never asked')
    settle(pool)
    assert pool.branches['a']['status'] == 'NO_REQUESTS'
    assert pool.available['max_requests'] == 6 - pool.branches['a']['slice']['max_requests']
    outcome = pool.finish()
    assert outcome['status'] == 'FAILED' and outcome['gaps'] == {'a': 'NO_REQUESTS'}


def test_complete_requires_every_declared_branch(tmp_path):
    pool = pool_for(tmp_path)
    pool.launch('a', ask(Adapter(), 'a'))
    outcome = pool.finish()
    assert outcome['status'] == 'PARTIAL' and outcome['results'] == {'a': 'a'} and outcome['unstarted'] == 2


@pytest.mark.parametrize('kind,verdict', [(HARD_ABORT, 'WITHHELD'), (CANCELLED, 'WITHHELD'), (SOFT_STOP, 'REUSE')])
def test_resume_does_not_reuse_results_the_pool_withheld(tmp_path, kind, verdict):
    pool, release, _ = blocked_scenario(tmp_path)
    pool.stop(kind, 'test')
    release.set()
    pool.finish()
    assert classify_branch_for_resume(tmp_path / 'pool' / 'branches' / 'a') == verdict


def test_deadline_triggers_soft_stop_through_validation(tmp_path):
    pool, release, late = blocked_scenario(tmp_path, requests=2)
    pool.started -= 1000  # the pool deadline passes while branch c is mid-request
    release.set()
    outcome = pool.finish()
    assert pool.stop_state == (SOFT_STOP, 'DEADLINE') and late.calls == 1
    assert outcome['status'] == 'PARTIAL' and outcome['results'] == {'a': 'a', 'b': 'b'}
    assert outcome['gaps'] == {'c': 'DISCARDED'}


def test_launch_after_finish_rolls_back(tmp_path):
    pool = pool_for(tmp_path)
    pool.finish()
    before = dict(pool.available)
    with pytest.raises(RuntimeError):
        pool.launch('late', ask(Adapter()))
    assert pool.available == before and 'late' not in pool.branches
    assert 'late' not in json.loads((tmp_path / 'pool' / 'pool.json').read_text())['branches']


def test_session_construction_failure_rolls_back(tmp_path, monkeypatch):
    pool = pool_for(tmp_path)
    before = dict(pool.available)

    def broken(*args):
        raise OSError('disk full')
    monkeypatch.setattr(branch_budget, 'TaskProviderSession', broken)
    with pytest.raises(OSError):
        pool.launch('a', ask(Adapter()))
    assert pool.available == before and pool.branches == {}


def test_finish_timeout_never_publishes_a_running_branch(tmp_path):
    pool, release, _ = blocked_scenario(tmp_path)
    outcome = pool.finish(0.2)  # c is still blocked inside its request
    assert outcome['results'] == {'a': 'a', 'b': 'b'} and outcome['gaps'] == {'c': 'RUNNING'}
    release.set()
    settle(pool)
    assert outcome['results'] == {'a': 'a', 'b': 'b'}  # the returned outcome is a snapshot
    assert 'c' not in outcome['results']
    again = pool.finish()  # the late writer finished after the seal: still never published
    assert again['results'] == {'a': 'a', 'b': 'b'} and again['gaps'] == {'c': 'DISCARDED'}


def test_pool_built_on_a_background_thread_with_router_reservation(tmp_path):
    router = TaskProviderSession(tmp_path / 'router', limits(), lambda: None)
    assert router.bind(Adapter(), 'route').complete(request()).action
    holder = {}

    def build():  # a new thread starts with an empty context; the reservation is set inside it
        with pass_router_reservation(router):
            holder['pool'] = pool_for(tmp_path, branches=2)
            holder['first'] = holder['pool'].launch('a', ask(Adapter()))
    thread = Thread(target=build)
    thread.start()
    thread.join(10)
    pool = holder['pool']
    other = Thread(target=lambda: holder.update(second=pool.launch('b', ask(Adapter()))))  # no reservation here
    other.start()
    other.join(10)
    assert holder['first'] == holder['second'] == 'RUNNING' and pool.total.max_requests == 5
    for name in 'ab':
        child = pool.branches[name]
        assert {key: getattr(child['session'].limits, key) for key in branch_budget._KEYS} == child['slice']
        assert child['slice']['max_requests'] == 2 and child['slice']['max_cost_usd'] == pytest.approx(.25)
    assert pool.finish()['status'] == 'COMPLETE'


def test_launch_beyond_the_declared_count_is_refused(tmp_path):
    pool = pool_for(tmp_path, branches=2, max_requests=8)
    pool.launch('a', ask(Adapter(), 'a'))
    pool.launch('b', ask(Adapter(), 'b'))
    settle(pool)
    available = dict(pool.available)
    with pytest.raises(ValueError, match='more branches'):
        pool.launch('c', ask(Adapter(), 'c'))
    assert pool.available == available and 'c' not in pool.branches
    assert 'c' not in json.loads((tmp_path / 'pool' / 'pool.json').read_text())['branches']
    outcome = pool.finish()
    assert outcome['status'] == 'COMPLETE' and outcome['gaps'] == {} and outcome['unstarted'] == 0


def test_one_failed_branch_of_two_is_partial_not_complete(tmp_path):
    pool = pool_for(tmp_path, branches=2)
    pool.launch('a', ask(Adapter(), 'a'))
    pool.launch('b', ask(Adapter(timeout_response), 'b'))
    outcome = pool.finish()
    assert outcome['status'] == 'PARTIAL' and outcome['results'] == {'a': 'a'} and outcome['unstarted'] == 0
    assert outcome['gaps'] == {'b': 'UNCERTAIN'}


def test_complete_never_coexists_with_gaps(tmp_path):
    pool = pool_for(tmp_path, branches=1)
    pool.launch('a', ask(Adapter(), 'a'))
    settle(pool)
    pool.branches['c'] = dict(status='FAILED', slice=None, held=None, result=None, session=None)  # forced
    outcome = pool.finish()  # published == declared, yet a gap exists: never COMPLETE
    assert outcome['status'] == 'PARTIAL' and outcome['gaps'] == {'c': 'FAILED'}


def pooled_branch(tmp_path):
    pool = pool_for(tmp_path, branches=2)
    pool.launch('a', ask(Adapter(), 'a'))
    settle(pool)
    return tmp_path / 'pool', tmp_path / 'pool' / 'branches' / 'a'


@pytest.mark.parametrize('content', ['{', '{"branches": {"a": {}}}', '{"stop": null}'])
def test_unreadable_or_incomplete_pool_journal_is_uncertain(tmp_path, content):
    root, branch = pooled_branch(tmp_path)
    assert classify_branch_for_resume(branch) == 'REUSE'
    (root / 'pool.json').write_text(content)
    assert classify_branch_for_resume(branch) == 'UNCERTAIN'


def test_branch_missing_from_the_journal_is_uncertain_not_withheld(tmp_path):
    root, branch = pooled_branch(tmp_path)
    journal = json.loads((root / 'pool.json').read_text())
    journal.update(stop=[HARD_ABORT, 'x'], branches={})
    (root / 'pool.json').write_text(json.dumps(journal))
    assert classify_branch_for_resume(branch) == 'UNCERTAIN'


def test_rollback_save_failure_keeps_the_original_error(tmp_path, monkeypatch):
    pool = pool_for(tmp_path)
    before = dict(pool.available)
    real, calls = pool._save, []

    def flaky():
        calls.append(1)
        if len(calls) > 1:
            raise OSError('journal gone')
        real()

    def broken(*args):
        raise ValueError('original')
    monkeypatch.setattr(pool, '_save', flaky)
    monkeypatch.setattr(branch_budget, 'TaskProviderSession', broken)
    with pytest.raises(ValueError, match='original') as caught:
        pool.launch('a', ask(Adapter()))
    assert any('journal gone' in note for note in caught.value.__notes__)
    assert pool.available == before and pool.branches == {}


def test_task_start_failure_closes_the_constructed_session(tmp_path, monkeypatch):
    pool = pool_for(tmp_path)
    before, closed = dict(pool.available), []
    real = TaskProviderSession.close
    monkeypatch.setattr(TaskProviderSession, 'close', lambda self, *a, **k: (closed.append(self), real(self, *a, **k)))

    def refuse(*args):
        raise RuntimeError('no start')
    monkeypatch.setattr(pool._tasks, 'start', refuse)
    with pytest.raises(RuntimeError, match='no start'):
        pool.launch('a', ask(Adapter()))
    assert len(closed) == 1 and pool.available == before and pool.branches == {}
    assert json.loads((tmp_path / 'pool' / 'pool.json').read_text())['branches'] == {}


def test_failed_session_without_attempts_is_failed_not_no_requests(tmp_path):
    pool = pool_for(tmp_path, branches=1)

    def work(session):
        session.failure = 'MODEL_TIMEOUT'
        return 'x'
    pool.launch('a', work)
    settle(pool)
    assert pool.branches['a']['status'] == 'FAILED'
    assert pool.finish()['gaps'] == {'a': 'FAILED'}

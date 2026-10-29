from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import sqlite3
from threading import Barrier

import pytest

from linkloom.tasks.models import TaskEventType, TaskStatus
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskSchemaError, TaskStore
from tests.unit.test_task_models import MEETING, make_candidate, make_evidence


REVIEWED = datetime(2026, 10, 20, 3, tzinfo=UTC)


def create(store, candidate=None, **options):
    return store.create_from_candidate('ws-a', candidate or make_candidate(), reviewer='审核人',
                                       reason='逐条核对原文后新建', now=REVIEWED, **options)


def revised(identifier='observation-b'):
    return make_candidate(identifier, owner='王芳', deadline='2026-10-16', deadline_text='延期到 10/16',
                          title='候选中的标题变化', meeting_time=MEETING + timedelta(days=2),
                          evidence=(make_evidence('meeting-b', 'version-b', '联调报告改由王芳负责。'),
                                    make_evidence('meeting-c', 'version-c', '延期到 10/16。', line=7)))


def test_multi_source_history_replays_exact_snapshot_in_append_order_even_at_same_timestamp(tmp_path):
    path = tmp_path / 'product.sqlite'
    with TaskStore(path) as store:
        initial = create(store, make_candidate(source_decision_id='decision-explicitly-provided'))
        assert initial.status is TaskStatus.OPEN
        changed = store.merge_candidate('ws-a', initial.id, revised(), apply_fields=['owner', 'deadline'],
                                         reviewer='复核人', reason='人工选中负责人和期限', now=REVIEWED)
        assert changed.title == initial.title and changed.owner == '王芳'
        assert (changed.deadline, changed.deadline_text) == ('2026-10-16', '延期到 10/16')
        assert changed.source_decision_id == initial.source_decision_id
        events = store.get_events('ws-a', initial.id)
        assert [e.event_type for e in events] == ['created', 'owner_changed', 'deadline_changed']
        assert len({e.created_at for e in events}) == 1
        assert [e.sequence for e in events] == sorted(e.sequence for e in events)
        assert events[0].new_value == initial.to_dict() and events[0].old_value is None
        assert events[1].old_value == '李明' and events[1].new_value == '王芳'
        assert events[2].old_value == {'deadline': '2026-10-12', 'deadline_text': '10/12 前完成'}
        assert events[2].new_value == {'deadline': '2026-10-16', 'deadline_text': '延期到 10/16'}
        assert events[0].meeting_time == MEETING
        for event in events[1:]:
            assert event.meeting_time == MEETING + timedelta(days=2)
            assert event.source_version_id is None
            assert event.evidence == revised().evidence
            assert [e.source_version_id for e in event.evidence] == ['version-b', 'version-c']
        assert store.replay_task('ws-a', initial.id) == changed
        link = store.get_link('ws-a', 'observation-b')
        assert link.review_action == 'merge' and link.task_id == initial.id
        assert link.candidate_fingerprint == revised().fingerprint
        for index, status in enumerate(('in_progress', 'blocked', 'done', 'cancelled'), 1):
            changed = store.set_status('ws-a', initial.id, status, actor='负责人', reason='手动更新',
                                       now=REVIEWED + timedelta(hours=index))
            assert store.replay_task('ws-a', initial.id) == changed
        renamed = store.merge_candidate('ws-a', initial.id, revised('observation-title'), apply_fields=['title'],
                                         reviewer='复核人', reason='人工确认标题', now=REVIEWED + timedelta(hours=5))
        assert renamed.title == revised().title
        assert store.replay_task('ws-a', initial.id) == renamed
        manual = [e for e in store.get_events('ws-a', initial.id) if e.event_type is TaskEventType.STATUS_CHANGED]
        assert len(manual) == 4
        assert all(e.evidence == () and e.meeting_time is None and e.source_version_id is None for e in manual)
    with TaskStore(path) as reopened:
        assert reopened.get_task('ws-a', initial.id) == renamed
        assert reopened.replay_task('ws-a', initial.id) == renamed
        assert reopened.get_events('ws-a', initial.id)[1].evidence == revised().evidence


@pytest.mark.parametrize('failure_at', ['created', 'deadline_changed', 'link'])
def test_failure_rolls_back_snapshot_all_events_and_candidate_receipt_and_allows_retry(tmp_path, failure_at):
    with TaskStore(tmp_path / 'rollback.sqlite') as store:
        original = None if failure_at == 'created' else create(store)
        history = () if original is None else store.get_events('ws-a', original.id)
        target = 'task_observation_link' if failure_at == 'link' else 'task_event'
        condition = '' if failure_at == 'link' else f"WHEN NEW.event_type='{failure_at}'"
        store._connection.execute(f'''CREATE TRIGGER injected_failure BEFORE INSERT ON {target} {condition}
                                     BEGIN SELECT RAISE(ABORT,'injected storage failure'); END''')
        def operation():
            if original is None:
                return create(store)
            return store.merge_candidate('ws-a', original.id, revised(), apply_fields=['owner', 'deadline'],
                                          reviewer='审核人', reason='人工合并', now=REVIEWED)
        with pytest.raises(sqlite3.IntegrityError, match='injected storage failure'):
            operation()
        assert store.list_tasks('ws-a') == (() if original is None else (original,))
        if original is not None:
            assert store.get_events('ws-a', original.id) == history
        assert store.get_link('ws-a', 'observation-a' if original is None else 'observation-b') is None
        store._connection.execute('DROP TRIGGER injected_failure')
        result = operation()
        assert store.replay_task('ws-a', result.id) == result


def test_duplicate_review_is_rejected_without_extra_tasks_events_or_links(tmp_path):
    with TaskStore(tmp_path / 'duplicate.sqlite') as store:
        task = create(store)
        before = store.get_events('ws-a', task.id)
        duplicate_operations = (
            lambda: create(store),
            lambda: store.merge_candidate('ws-a', task.id, make_candidate(), apply_fields=['owner'],
                                           reviewer='审核人', reason='重复请求'),
            lambda: store.reject_candidate('ws-a', make_candidate(), reviewer='审核人', reason='重复忽略'),
        )
        for operation in duplicate_operations:
            with pytest.raises(CandidateAlreadyReviewedError):
                operation()
            assert store.list_tasks('ws-a') == (task,)
            assert store.get_events('ws-a', task.id) == before
        ignored = make_candidate('ignored')
        receipt = store.reject_candidate('ws-a', ignored, reviewer='审核人', reason='人工忽略', now=REVIEWED)
        assert receipt.task_id is None and receipt.review_action == 'reject'
        assert store.get_link('ws-a', 'ignored') == receipt
        with pytest.raises(CandidateAlreadyReviewedError):
            store.reject_candidate('ws-a', ignored, reviewer='审核人', reason='重复忽略')
        with pytest.raises(CandidateAlreadyReviewedError):
            create(store, ignored)
        assert store.list_tasks('ws-a') == (task,) and store.get_events('ws-a', task.id) == before


@pytest.mark.parametrize('connection_count', [1, 2])
def test_simultaneous_candidate_reviews_commit_exactly_once(tmp_path, connection_count):
    path = tmp_path / 'concurrent.sqlite'
    stores = [TaskStore(path) for _ in range(connection_count)]
    barrier = Barrier(8)
    def review(index):
        barrier.wait(timeout=10)
        try:
            return create(stores[index % connection_count]).id
        except CandidateAlreadyReviewedError:
            return None
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(review, range(8)))
        assert sum(result is not None for result in results) == 1
        tasks = stores[0].list_tasks('ws-a')
        assert len(tasks) == 1 and len(stores[0].get_events('ws-a', tasks[0].id)) == 1
        assert stores[0].replay_task('ws-a', tasks[0].id) == tasks[0]
    finally:
        for store in stores:
            store.close()


def test_workspace_filters_and_writes_cannot_cross_the_task_boundary(tmp_path):
    with TaskStore(tmp_path / 'scoped.sqlite') as store:
        task = create(store)
        other = store.create_from_candidate('ws-b', make_candidate('other', workspace='ws-b', owner=None),
                                            reviewer='审核人', reason='另一空间', now=REVIEWED)
        unassigned = create(store, make_candidate('unassigned', owner=None))
        store.set_status('ws-a', task.id, 'blocked', actor='负责人', reason='人工标记', now=REVIEWED)
        assert {t.id for t in store.list_tasks('ws-a')} == {task.id, unassigned.id}
        assert [t.id for t in store.list_tasks('ws-a', owner='李明', status='blocked')] == [task.id]
        assert store.list_tasks('ws-a', owner=None) == (unassigned,)
        assert store.list_tasks('ws-b') == (other,)
        assert store.get_task('ws-b', task.id) is None
        assert store.get_events('ws-b', task.id) == ()
        assert store.get_link('ws-b', 'observation-a') is None
        with pytest.raises(TaskNotFoundError):
            store.replay_task('ws-b', task.id)
        foreign_candidate = make_candidate('foreign-target', workspace='ws-b')
        with pytest.raises(TaskNotFoundError):
            store.merge_candidate('ws-b', task.id, foreign_candidate, apply_fields=['owner'],
                                   reviewer='审核人', reason='不得越权')
        assert store.get_link('ws-b', foreign_candidate.candidate_id) is None
        with pytest.raises(TaskNotFoundError):
            store.set_status('ws-b', task.id, 'done', actor='用户', reason='不得越权')
        with pytest.raises(ValueError, match='workspace'):
            store.create_from_candidate('ws-b', make_candidate('wrong-workspace'), reviewer='用户', reason='错误空间')
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            store._connection.execute('''INSERT INTO task_observation_link VALUES
                ('forged','ws-b',?,'merge','user','forged',?,?)''',
                (task.id, REVIEWED.isoformat(), '0' * 64))
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            store._connection.execute('''INSERT INTO task_event(id,task_id,workspace_id,event_type,old_value,
                new_value,source_version_id,evidence_json,meeting_time,actor,reason,created_at)
                SELECT 'forged-event',task_id,'ws-b',event_type,old_value,new_value,source_version_id,
                evidence_json,meeting_time,actor,reason,created_at FROM task_event WHERE task_id=? LIMIT 1''', (task.id,))


def test_selected_null_fields_clear_only_selected_values_and_no_op_review_is_still_receipted(tmp_path):
    with TaskStore(tmp_path / 'select.sqlite') as store:
        task = create(store)
        cleared = store.merge_candidate('ws-a', task.id, make_candidate('clear', owner=None, deadline=None,
                                        deadline_text='时间待定', title='不应用此标题'), apply_fields=['owner'],
                                        reviewer='用户', reason='仅清空负责人', now=REVIEWED)
        assert cleared.owner is None and cleared.deadline == task.deadline
        assert cleared.deadline_text == task.deadline_text and cleared.title == task.title
        unchanged = store.merge_candidate('ws-a', task.id, make_candidate('no-op'), apply_fields=[],
                                           reviewer='用户', reason='仅关联，不应用字段', now=REVIEWED + timedelta(hours=1))
        assert unchanged == cleared
        assert store.get_link('ws-a', 'no-op').task_id == task.id
        assert len(store.get_events('ws-a', task.id)) == 2
        assert store.replay_task('ws-a', task.id) == cleared
        assert store.set_status('ws-a', task.id, 'open', actor='用户', reason='状态未改变', now=REVIEWED) == cleared
        assert len(store.get_events('ws-a', task.id)) == 2


def test_events_are_append_only_and_invalid_commands_have_no_side_effects(tmp_path):
    with TaskStore(tmp_path / 'immutable.sqlite') as store:
        task = create(store)
        before = store.get_events('ws-a', task.id)
        for sql in ('UPDATE task_event SET reason=\'rewritten\'', 'DELETE FROM task_event'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                store._connection.execute(sql)
        for values in ({'reviewer': '', 'reason': 'reason'}, {'reviewer': 'user', 'reason': ''}):
            with pytest.raises(ValueError):
                store.create_from_candidate('ws-a', revised(), **values)
        with pytest.raises(ValueError):
            store.set_status('ws-a', task.id, 'completed', actor='user', reason='unsupported enum')
        with pytest.raises(ValueError):
            store.merge_candidate('ws-a', task.id, revised(), apply_fields=['status'], reviewer='user', reason='bad field')
        with pytest.raises(ValueError):
            store.merge_candidate('ws-a', task.id, revised(), apply_fields=['owner','owner'], reviewer='user', reason='duplicate')
        assert store.get_task('ws-a', task.id) == task
        assert store.get_events('ws-a', task.id) == before and store.get_link('ws-a', 'observation-b') is None


def test_additive_initialization_preserves_existing_data_and_user_version_and_rejects_collisions(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE untouched (id INTEGER PRIMARY KEY, content TEXT)')
        connection.execute("INSERT INTO untouched VALUES (1,'original')")
        connection.execute('PRAGMA user_version=42')
    for _ in range(2):
        with TaskStore(path) as store:
            assert store._connection.execute('PRAGMA user_version').fetchone()[0] == 42
            assert tuple(store._connection.execute('SELECT * FROM untouched').fetchone()) == (1, 'original')
    collision = tmp_path / 'foreign-task.sqlite'
    with sqlite3.connect(collision) as connection:
        connection.execute('CREATE TABLE task (original TEXT)')
        connection.execute("INSERT INTO task VALUES ('preserve')")
    with pytest.raises(TaskSchemaError):
        TaskStore(collision)
    with sqlite3.connect(collision) as connection:
        assert connection.execute('SELECT original FROM task').fetchone()[0] == 'preserve'
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='task_schema_metadata'").fetchone() is None
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE task_schema_metadata SET version=99')
    with pytest.raises(TaskSchemaError, match='version'):
        TaskStore(path)


def test_simultaneous_first_opens_share_one_schema_and_all_connections_can_read_and_write(tmp_path):
    # Each round exercises a genuinely new database; no store is initialized
    # before the four clients are released together.
    for round_number in range(8):
        path = tmp_path / f'first-open-{round_number}.sqlite'
        barrier = Barrier(4)

        def open_store():
            barrier.wait(timeout=10)
            return TaskStore(path)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(open_store) for _ in range(4)]
        try:
            stores = [future.result() for future in futures]
            with closing(sqlite3.connect(path)) as connection:
                assert connection.execute('SELECT id,version FROM task_schema_metadata').fetchall() == [(1, 1)]
            tasks = [create(store, make_candidate(f'first-{index}')) for index, store in enumerate(stores)]
            expected_ids = {task.id for task in tasks}
            assert len(expected_ids) == 4
            for store, task in zip(stores, tasks):
                assert {item.id for item in store.list_tasks('ws-a')} == expected_ids
                assert store.get_task('ws-a', task.id) == task
                assert store.replay_task('ws-a', task.id) == task
        finally:
            # Successful opens must also be closed if another future raised.
            for future in futures:
                if future.exception() is None:
                    future.result().close()

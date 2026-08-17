import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from linkloom.mutations.models import ChangePlan, Approval, ChangeOperation, Precondition
from linkloom.mutations.runtime import WritebackStore, WritebackRuntime, WritebackState, InvalidStateTransitionError
from linkloom.mutations.operations import build_append_plan
from linkloom.observability.events import EventEmitter
from linkloom.observability.sinks import JsonlEventSink

@pytest.fixture
def tmp_workdir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    return root, backup

@pytest.fixture
def store():
    return WritebackStore(":memory:")

@pytest.fixture
def event_sink(tmp_path):
    return JsonlEventSink(tmp_path)

def test_sqlite_persistence_new_instance():
    db_path = tempfile.mktemp()
    try:
        store1 = WritebackStore(db_path)
        state1 = WritebackState(
            run_id="run_1",
            thread_id="th_1",
            status="pending",
            plan_id="plan_1",
            plan_sha256="hash",
            target_root_fingerprint="fp",
            operation_ids=["op_1", "op_2"],
            last_event_seq=5,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat()
        )
        store1.save(state1)
        
        store2 = WritebackStore(db_path)
        state2 = store2.load("run_1")
        assert state2 is not None
        assert state2.run_id == "run_1"
        assert state2.operation_ids == ["op_1", "op_2"]
        assert state2.last_event_seq == 5
    finally:
        store1.close()
        store2.close()
        if os.path.exists(db_path):
            os.remove(db_path)

def test_lifecycle_happy_path(tmp_workdir, event_sink):
    root, backup = tmp_workdir
    test_file = root / "test.md"
    test_file.write_text("initial\n", encoding="utf-8")
    
    # 1. build_append_plan (Integration of build_append_plan with runtime)
    plan = build_append_plan(
        run_id="run_2",
        root=root,
        fingerprint="synth_fp",
        kind="append_block",
        target_relative_path="test.md",
        append_text="appended\n",
        evidence_refs=["ref1"],
        source_index_hash="hash1",
        risk="low"
    )
    
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run_2", "th_1", event_sink)
    runtime = WritebackRuntime(store, emitter)
    
    # create
    state = runtime.create_run(plan)
    assert state.status == "pending"
    assert state.last_event_seq > 0
    
    # request approval
    state = runtime.request_approval(state)
    assert state.status == "awaiting_approval"
    
    # approve
    approval = Approval(
        approval_id="app_1",
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        target_root_fingerprint="synth_fp",
        approved_operation_ids=[op.operation_id for op in plan.operations],
        decision="approve",
        actor="human",
        confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    assert state.status == "approved"
    
    # pause
    state = runtime.checkpoint_pause(state)
    assert state.status == "paused"
    
    # load state from store to simulate resume from paused
    loaded_state = store.load(state.run_id)
    assert loaded_state.status == "paused"
    
    # resume
    runtime.resume(loaded_state, plan, approval, root, backup)
    final_state = store.load(state.run_id)
    
    assert final_state.status == "applied"
    assert "appended\n" in test_file.read_text(encoding="utf-8")
    
    # verify trace seq
    lines = event_sink.read_lines()
    seqs = []
    import json
    for line in lines:
        evt = json.loads(line)
        seqs.append(evt["seq"])
        
        # ensure no absolute path in attrs
        attrs = evt.get("attributes", {})
        for k, v in attrs.items():
            if isinstance(v, str) and (v.startswith("/") or ":\\" in v):
                pytest.fail(f"Absolute path found in trace: {v}")
    
    # Check contiguous seq
    assert seqs == list(range(1, len(seqs) + 1))

def test_reject_approval(tmp_workdir, event_sink):
    root, backup = tmp_workdir
    plan = ChangePlan(
        plan_id="p1", plan_sha256="hash", created_from_run_id="run", source_index_sha256="hash",
        target_root_fingerprint="fp", operations=[], operation_count=0, diff_summary={},
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        schema_version=1, target_root_mode="synthetic_fixture_only", approval_status="pending"
    )
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    
    approval = Approval(
        approval_id="app_1", plan_id="p1", plan_sha256="hash", target_root_fingerprint="fp",
        approved_operation_ids=[], decision="reject", actor="human", confirmation_phrase="no",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.reject_approval(state, approval)
    assert state.status == "rejected"

def test_stale_hash_after_pause(tmp_workdir, event_sink):
    root, backup = tmp_workdir
    test_file = root / "test.md"
    test_file.write_text("initial\n", encoding="utf-8")
    
    plan = build_append_plan(
        run_id="run_2", root=root, fingerprint="synth_fp", kind="append_block",
        target_relative_path="test.md", append_text="appended\n", evidence_refs=["ref1"],
        source_index_hash="hash1", risk="low"
    )
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run_2", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    
    approval = Approval(
        approval_id="app_1", plan_id=plan.plan_id, plan_sha256=plan.plan_sha256,
        target_root_fingerprint="synth_fp", approved_operation_ids=[op.operation_id for op in plan.operations],
        decision="approve", actor="human", confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    state = runtime.checkpoint_pause(state)
    
    # Mutate file to make hash stale
    test_file.write_text("mutated\n", encoding="utf-8")
    
    state = runtime.resume(state, plan, approval, root, backup)
    assert state.status == "stale"
    assert state.error_code == "STALE_HASH"
    assert test_file.read_text(encoding="utf-8") == "mutated\n"

def test_duplicate_approval(tmp_workdir, event_sink):
    root, backup = tmp_workdir
    plan = ChangePlan(
        plan_id="p1", plan_sha256="hash", created_from_run_id="run", source_index_sha256="hash",
        target_root_fingerprint="fp", operations=[], operation_count=0, diff_summary={},
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        schema_version=1, target_root_mode="synthetic_fixture_only", approval_status="pending"
    )
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    
    approval = Approval(
        approval_id="app_1", plan_id="p1", plan_sha256="hash", target_root_fingerprint="fp",
        approved_operation_ids=[], decision="approve", actor="human", confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    
    state = runtime.accept_approval(state, approval)
    assert state.status == "approved"
    
    events = event_sink.read_lines()
    import json
    last_event = json.loads(events[-1])
    assert last_event["event_type"] == "writeback.failed"
    assert last_event["error"]["code"] == "DUPLICATE_APPROVAL"

def test_resume_terminal(tmp_workdir, event_sink):
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    state = WritebackState(run_id="r1", thread_id="t1", status="applied", plan_id="p1", plan_sha256="h", target_root_fingerprint="f", operation_ids=[])
    
    state2 = runtime.resume(state, None, None, None, None)
    assert state2.status == "applied"
    
    events = event_sink.read_lines()
    import json
    last_event = json.loads(events[-1])
    assert last_event["event_type"] == "writeback.failed"
    assert last_event["error"]["code"] == "RESUME_TERMINAL"

def test_writer_failure_rollback(tmp_workdir, event_sink, monkeypatch):
    root, backup = tmp_workdir
    test_file = root / "test.md"
    test_file.write_text("initial\n", encoding="utf-8")
    
    import os
    orig_replace = os.replace
    def mock_replace(src, dst):
        if ".tmp_" in str(src):
            raise RuntimeError("Mock writer failure")
        orig_replace(src, dst)
    monkeypatch.setattr(os, "replace", mock_replace)
    
    plan = build_append_plan(
        run_id="run_2", root=root, fingerprint="synth_fp", kind="append_block",
        target_relative_path="test.md", append_text="appended\n", evidence_refs=["ref1"],
        source_index_hash="hash1", risk="low"
    )
    
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run_2", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    
    approval = Approval(
        approval_id="app_1", plan_id=plan.plan_id, plan_sha256=plan.plan_sha256,
        target_root_fingerprint="synth_fp", approved_operation_ids=[op.operation_id for op in plan.operations],
        decision="approve", actor="human", confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    
    state = runtime.resume(state, plan, approval, root, backup)
        
    assert state.status == "rolled_back"
    assert test_file.read_text(encoding="utf-8") == "initial\n"

def test_resume_stale_preflight_no_apply(tmp_workdir, event_sink, monkeypatch):
    root, backup = tmp_workdir
    test_file = root / "test.md"
    test_file.write_text("initial\n", encoding="utf-8")
    
    call_count = [0]
    import linkloom.mutations.runtime
    orig_apply = linkloom.mutations.runtime.apply_plan
    def mock_apply(*args, **kwargs):
        call_count[0] += 1
        return orig_apply(*args, **kwargs)
    monkeypatch.setattr(linkloom.mutations.runtime, "apply_plan", mock_apply)

    plan = build_append_plan(
        run_id="run_2", root=root, fingerprint="synth_fp", kind="append_block",
        target_relative_path="test.md", append_text="appended\n", evidence_refs=["ref1"],
        source_index_hash="hash1", risk="low"
    )
    store = WritebackStore(":memory:")
    emitter = EventEmitter("run_2", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    approval = Approval(
        approval_id="app_1", plan_id=plan.plan_id, plan_sha256=plan.plan_sha256,
        target_root_fingerprint="synth_fp", approved_operation_ids=[op.operation_id for op in plan.operations],
        decision="approve", actor="human", confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    
    test_file.write_text("mutated\n", encoding="utf-8")
    
    state = runtime.resume(state, plan, approval, root, backup)
    assert state.status == "stale"
    assert state.error_code == "STALE_HASH"
    assert call_count[0] == 0

def test_resume_new_instance(tmp_workdir, tmp_path):
    root, backup = tmp_workdir
    test_file = root / "test.md"
    test_file.write_text("initial\n", encoding="utf-8")
    
    db_path = tmp_path / "test.db"
    store = WritebackStore(db_path.as_posix())
    event_sink = JsonlEventSink(tmp_path)
    emitter = EventEmitter("run_2", "th", event_sink)
    runtime = WritebackRuntime(store, emitter)
    
    plan = build_append_plan(
        run_id="run_2", root=root, fingerprint="synth_fp", kind="append_block",
        target_relative_path="test.md", append_text="appended\n", evidence_refs=["ref1"],
        source_index_hash="hash1", risk="low"
    )
    
    state = runtime.create_run(plan)
    state = runtime.request_approval(state)
    approval = Approval(
        approval_id="app_1", plan_id=plan.plan_id, plan_sha256=plan.plan_sha256,
        target_root_fingerprint="synth_fp", approved_operation_ids=[op.operation_id for op in plan.operations],
        decision="approve", actor="human", confirmation_phrase="yes",
        created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    state = runtime.accept_approval(state, approval)
    state = runtime.checkpoint_pause(state)
    
    store.close()
    
    store2 = WritebackStore(db_path.as_posix())
    event_sink2 = JsonlEventSink(tmp_path)
    emitter2 = EventEmitter("run_2", "th", event_sink2)
    runtime2 = WritebackRuntime(store2, emitter2)
    
    state_loaded = store2.load(state.run_id)
    state2 = runtime2.resume(state_loaded, plan, approval, root, backup)
    assert state2.status == "applied"
    
    events = event_sink2.read_lines()
    seqs = []
    import json
    for line in events:
        evt = json.loads(line)
        seqs.append(evt["seq"])
    assert seqs == list(range(1, len(seqs) + 1))
    
    store2.close()

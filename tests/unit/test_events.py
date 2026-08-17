import pytest
from dataclasses import dataclass
from linkloom.observability.events import TraceEvent, EventEmitter, ValidationError

@dataclass
class DummyErrorEnvelope:
    code: str
    message: str
    
    def to_dict(self):
        return {"code": self.code, "message": self.message}

class DummySink:
    def __init__(self):
        self.events = []
    
    def append(self, event):
        self.events.append(event)

def test_trace_event_valid_creation():
    event = TraceEvent(
        event_id="evt_1",
        run_id="run_1",
        thread_id="thread_1",
        seq=1,
        event_type="step.started",
        actor="runtime",
        status="started",
        redaction={"policy_version": "trace-redaction-v1"},
        started_at="2026-08-16T00:00:02Z"
    )
    assert event.event_id == "evt_1"

def test_trace_event_invalid_type():
    with pytest.raises(ValidationError, match="Unknown event_type"):
        TraceEvent(
            event_id="evt_1", run_id="run_1", thread_id="thread_1", seq=1,
            event_type="invalid_type", actor="runtime", status="started", redaction={}
        )

def test_trace_event_seq_monotonic():
    sink = DummySink()
    emitter = EventEmitter("run_1", "thread_1", sink)
    e1 = emitter.emit("step.started", "runtime", "started", redaction={})
    e2 = emitter.emit("step.completed", "runtime", "ok", redaction={})
    assert e1.seq == 1
    assert e2.seq == 2
    assert sink.events == [e1, e2]

def test_trace_event_rfc3339_validation():
    with pytest.raises(ValidationError, match="Invalid started_at format"):
        TraceEvent(
            event_id="evt_1", run_id="run_1", thread_id="t_1", seq=1,
            event_type="step.started", actor="runtime", status="started",
            redaction={}, started_at="2026/08/16 00:00:00"
        )

def test_trace_event_failed_needs_error():
    with pytest.raises(ValidationError, match="Error envelope is required"):
        TraceEvent(
            event_id="evt_1", run_id="run_1", thread_id="t_1", seq=1,
            event_type="step.failed", actor="runtime", status="failed",
            redaction={}
        )

    # valid with error
    TraceEvent(
        event_id="evt_1", run_id="run_1", thread_id="t_1", seq=1,
        event_type="step.failed", actor="runtime", status="failed",
        redaction={}, error=DummyErrorEnvelope("err_1", "msg")
    )
    
def test_event_emitter_jsonl_append(tmp_path):
    from linkloom.observability.sinks import JsonlEventSink
    import json
    sink = JsonlEventSink(tmp_path)
    emitter = EventEmitter("run_1", "thread_1", sink)
    emitter.emit("step.started", "runtime", "started", redaction={})
    emitter.emit("step.completed", "runtime", "ok", redaction={})
    
    with open(tmp_path / "events.jsonl", "r") as f:
        lines = f.readlines()
        assert len(lines) == 2
        d1 = json.loads(lines[0])
        d2 = json.loads(lines[1])
        assert d1["seq"] == 1
        assert d2["seq"] == 2
import json
from linkloom.observability.reader import TraceReader, TraceManifest, SpanRecord, TraceReadError
from linkloom.observability.summary import summarize_trace

def test_trace_reader_valid(tmp_path):
    run_dir = tmp_path / "run_ok"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    
    e1 = {
        "event_id": "e1", "run_id": "run_ok", "thread_id": "t1", "seq": 1,
        "event_type": "step.started", "actor": "runtime", "status": "started",
        "redaction": {"policy_version": "trace-redaction-v1"}, "schema_version": 1
    }
    e2 = {
        "event_id": "e2", "run_id": "run_ok", "thread_id": "t1", "seq": 2,
        "event_type": "step.completed", "actor": "runtime", "status": "ok",
        "redaction": {"policy_version": "trace-redaction-v1"}, "schema_version": 1
    }
    events_path.write_text(json.dumps(e1) + "\n" + json.dumps(e2) + "\n")
    
    reader = TraceReader(tmp_path)
    events = reader.read_events("run_ok")
    assert len(events) == 2
    assert events[0].seq == 1
    assert events[1].seq == 2

def test_trace_reader_corrupt_json(tmp_path):
    run_dir = tmp_path / "run_bad"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    events_path.write_text("{\"event_id\": \"e1\", \n")
    
    reader = TraceReader(tmp_path)
    with pytest.raises(TraceReadError, match="Corrupted JSON"):
        reader.read_events("run_bad")

def test_trace_reader_seq_jump(tmp_path):
    run_dir = tmp_path / "run_jump"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    
    e1 = {
        "event_id": "e1", "run_id": "run_jump", "thread_id": "t1", "seq": 1,
        "event_type": "step.started", "actor": "runtime", "status": "started"
    }
    e2 = {
        "event_id": "e2", "run_id": "run_jump", "thread_id": "t1", "seq": 3,
        "event_type": "step.completed", "actor": "runtime", "status": "ok"
    }
    events_path.write_text(json.dumps(e1) + "\n" + json.dumps(e2) + "\n")
    
    reader = TraceReader(tmp_path)
    with pytest.raises(TraceReadError, match="seq jump"):
        reader.read_events("run_jump")


def test_trace_reader_missing_parent_event(tmp_path):
    run_dir = tmp_path / "run_parent"
    run_dir.mkdir()
    event = {
        "event_id": "e1", "run_id": "run_parent", "thread_id": "t1", "seq": 1,
        "event_type": "step.started", "actor": "runtime", "status": "started",
        "parent_event_id": "missing-parent",
    }
    (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n")
    with pytest.raises(TraceReadError, match="missing parent_event_id"):
        TraceReader(tmp_path).read_events("run_parent")

def test_trace_reader_run_id_mismatch(tmp_path):
    run_dir = tmp_path / "run_a"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    
    e1 = {
        "event_id": "e1", "run_id": "run_b", "thread_id": "t1", "seq": 1,
        "event_type": "step.started", "actor": "runtime", "status": "started"
    }
    events_path.write_text(json.dumps(e1) + "\n")
    
    reader = TraceReader(tmp_path)
    with pytest.raises(TraceReadError, match="run_id mismatch"):
        reader.read_events("run_a")

def test_trace_manifest_roundtrip(tmp_path):
    manifest = TraceManifest(
        trace_schema_version=1,
        run_id="run_1",
        event_count=5,
        first_seq=1,
        last_seq=5,
        event_log="events.jsonl",
        summary="trace_summary.md",
        source_index_sha256="a"*64,
        redaction_policy_version="trace-redaction-v1",
        complete=True
    )
    reader = TraceReader(tmp_path)
    reader.write_manifest(manifest)
    
    loaded = reader.read_manifest("run_1")
    assert loaded.run_id == "run_1"
    assert loaded.event_count == 5

def test_trace_manifest_invalid_sha(tmp_path):
    with pytest.raises(ValueError, match="valid 64-character lowercase hex"):
        TraceManifest(
            trace_schema_version=1, run_id="run", event_count=1,
            first_seq=1, last_seq=1, event_log="events.jsonl",
            summary="trace_summary.md", source_index_sha256="invalid",
            redaction_policy_version="trace-redaction-v1", complete=True
        )

def test_span_record_validation():
    with pytest.raises(ValueError, match="Invalid kind"):
        SpanRecord("s1", "r1", None, "name", "invalid_kind", "e1", "e2", "ok", 10)
    with pytest.raises(ValueError, match="Invalid status"):
        SpanRecord("s1", "r1", None, "name", "tool", "e1", "e2", "bad_status", 10)
    with pytest.raises(ValueError, match="non-negative"):
        SpanRecord("s1", "r1", None, "name", "tool", "e1", "e2", "ok", -1)
    
    SpanRecord("s1", "r1", None, "name", "tool", "e1", "e2", "ok", 10, "a"*64, "b"*64)

def test_summary_generation(tmp_path):
    e1 = TraceEvent("e1", "r1", "t1", 1, "run.started", "runtime", "started", duration_ms=10)
    e2 = TraceEvent("e2", "r1", "t1", 2, "tool.called", "agent", "started", duration_ms=20, redaction={"secrets_detected": 1})
    e3 = TraceEvent("e3", "r1", "t1", 3, "run.completed", "runtime", "ok", duration_ms=30)
    
    events = [e1, e2, e3]
    manifest = TraceManifest(
        1, "r1", 3, 1, 3, "events.jsonl", "trace_summary.md", "a"*64, "trace-redaction-v1", True
    )
    summary = summarize_trace(events, manifest)
    assert "r1" in summary
    assert "**Tool Calls:** 1" in summary
    assert "**Total Duration:** 60ms" in summary
    assert "**Secrets Detected:** 1" in summary


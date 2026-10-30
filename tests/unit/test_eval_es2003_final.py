"""Offline boundary tests for held-out wiring; no real corpus or model calls."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location("ami_final_eval", Path(__file__).resolve().parents[2] / "scripts/eval_es2003_final.py")
final = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = final
SPEC.loader.exec_module(final)


def test_freeze_rejects_source_or_gold_changes_without_parsing_gold(tmp_path, monkeypatch):
    source = tmp_path / "frozen.py"
    source.write_bytes(b"frozen\r\n")
    metadata = tmp_path / "benchmarks/ami_tasks/v2_frozen.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"source_normalized_sha256": {"frozen.py": final.sha(b"frozen\n")}}))
    gold = tmp_path / "gold.jsonl"
    gold.write_bytes(b"This is deliberately not JSON; verification only hashes bytes.")
    monkeypatch.setattr(final, "ROOT", tmp_path)
    monkeypatch.setattr(final, "GOLD_SHA256", final.sha(gold.read_bytes()))
    monkeypatch.setattr(final.subprocess, "check_output", lambda args, **kwargs: b"frozen\n" if args[1] == "show" else "commit\n")
    assert final.verify_freeze(gold)["gold_sha256"] == final.GOLD_SHA256
    gold.write_bytes(b"changed")
    with pytest.raises(ValueError, match="gold hash mismatch"):
        final.verify_freeze(gold)
    source.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="frozen source mismatch"):
        final.verify_freeze(gold)


def test_every_prediction_is_saved_before_scoring_and_limits_are_shared(tmp_path, monkeypatch):
    events = []
    meetings = [{"meeting_id": f"sample-{i}"} for i in range(4)]
    client_ids, ceilings = set(), []
    def baseline(meeting, client):
        client_ids.add(id(client.counter))
        ceilings.append(client.gemini_max_input_bytes)
        events.append(("baseline", meeting["meeting_id"]))
        return [{"task": "Ship the package"}]
    def linkloom(meeting, client, **kwargs):
        client_ids.add(id(client.counter))
        events.append((kwargs["task_extraction_mode"], meeting["meeting_id"]))
        assert kwargs["speaker_wiring"] is True
        expected = 192 * 1024 if kwargs["task_extraction_mode"] == "meeting_v2" else 32768
        assert client.gemini_max_input_bytes == expected
        return [{"task": "Ship the package"}], []
    def score(meetings, gold, out, client, predictions, diagnostics):
        assert len(events) == 12
        assert all(set(p) == set(final.SYSTEMS) for p in predictions.values())
        assert (out / "predictions.json").is_file()
        assert client.counter.max_requests == 80 and client.counter.max_usd == 1.5
        events.append(("gold parsed for scoring", ""))
        return {}, []
    monkeypatch.setattr(final, "verify_freeze", lambda p: {"source_normalized_sha256": {}})
    monkeypatch.setattr(final, "load_inputs", lambda *args: (meetings, {}))
    monkeypatch.setattr(final.ev, "baseline_predict", baseline)
    monkeypatch.setattr(final.ev, "linkloom_predict", linkloom)
    monkeypatch.setattr(final, "score_all", score)
    monkeypatch.setattr(final, "write_report", lambda *args: None)
    final.run(tmp_path, tmp_path, tmp_path / "never-read-gold", tmp_path / "out", live=True)
    assert len(client_ids) == 1
    assert events[-1][0] == "gold parsed for scoring"


def test_provider_failure_is_not_retried_and_no_gold_scoring_runs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(final, "verify_freeze", lambda p: {"source_normalized_sha256": {}})
    monkeypatch.setattr(final, "load_inputs", lambda *args: ([{"meeting_id": "sample"}], {}))
    def failed(meeting, client):
        calls.append(1)
        client.counter.reserve(final.ev.GEMINI, {"input": "fixture"}, 100)
        raise RuntimeError("provider failure")
    monkeypatch.setattr(final.ev, "baseline_predict", failed)
    monkeypatch.setattr(final, "score_all", lambda *args: pytest.fail("gold may not be read after incomplete predictions"))
    with pytest.raises(RuntimeError, match="provider failure"):
        final.run(tmp_path, tmp_path, tmp_path / "never-read-gold", tmp_path / "out", live=True)
    assert len(calls) == 1
    failure = json.loads((tmp_path / "out/failure.json").read_text())
    assert failure["counter"]["requests"] == 1
    assert failure["counter"]["reserved_upper_bound_usd"] > 0


def test_audit_sample_covers_all_three_systems_and_leaves_judgments_unchanged():
    rows = [{"system": system, "meeting_id": "sample", "prediction_id": str(i),
             "gold_id": None, "category": "DISCUSSION"}
            for system in final.SYSTEMS for i in range(6)]
    snapshot = json.dumps(rows, sort_keys=True)
    sampled = final.sample_rows(rows)
    assert len(sampled) == 6
    assert all(sum(r["system"] == s for r in sampled) == 2 for s in final.SYSTEMS)
    assert sampled == final.sample_rows(rows)
    assert json.dumps(rows, sort_keys=True) == snapshot

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


def test_required_rejudgment_sample_preserves_counts_and_uses_an_actual_prediction():
    rows = [{"system": s, "meeting_id": "ES2003b", "prediction_id": str(i),
             "gold_id": None, "category": "DISCUSSION"}
            for s in final.SYSTEMS for i in range(10)]
    rows += [{"system": "legacy", "meeting_id": "ES2003a", "prediction_id": "retry-prediction",
              "gold_id": "g1", "category": "MATCH"},
             {"system": "legacy", "meeting_id": "ES2003a", "prediction_id": "",
              "gold_id": "g2", "category": "FN"}]
    sampled = final.sample_rows(rows, required=("legacy", "ES2003a"))
    assert any(r["prediction_id"] == "retry-prediction" for r in sampled)
    assert [sum(r["system"] == s for r in sampled) for s in final.SYSTEMS] == [2, 3, 2]
    assert sampled == final.sample_rows(rows, required=("legacy", "ES2003a"))


def test_invalid_replacement_stops_before_any_other_meeting_and_keeps_cumulative_spend(tmp_path, monkeypatch):
    import random
    mid = "ES2003a"
    text = "X [0.000-1.000]: I will write and send the note.\n"
    gold = {"gold_id": "ES2003a-c001", "meeting_id": mid, "quote": text,
            "task": "Write and send the note", "owner": "Coordinator", "deadline": "未说明", "notes": ""}
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    meeting = {"meeting_id": mid, "text": text,
               "speakers": [{"speaker": "X", "participant_id": "person-x", "role": "Coordinator"}]}
    pred = {"task": "Write and send the note", "quote": text, "owner": "Coordinator", "deadline": "未说明"}
    predictions = {mid: {"baseline": [pred], "legacy": [pred, pred], "v2": []}}
    def payload(system, count):
        rng = random.Random(f"20261029:{mid}:{system}")
        return {"judgments": [{"prediction_id": f"p-{rng.getrandbits(64):016x}",
            "gold_id": gold["gold_id"], "category": "MATCH", "owner_correct": True, "deadline_correct": False,
            "reason": "明确承诺。", "evidence_quote": text, "prediction_task_zh": "写好并发送便条",
            "evidence_quote_zh": "我会写好并发送便条。"} for _ in range(count)],
            "gold_translations": [{"gold_id": gold["gold_id"], "task_zh": "写好并发送便条", "quote_zh": "我会写好并发送便条。"}]}
    source = tmp_path / "source"
    (source / "judge_legacy").mkdir(parents=True)
    (source / "judge_legacy/ES2003a.baseline.judge.json").write_text(json.dumps(payload("baseline", 1)))
    prior = {"counter": {"requests": 28, "reserved_upper_bound_usd": 0.2},
             "actual_cost_usd_from_usage": 0.2, "usage_by_model": {}}
    client = final.JudgeContinuationClient(prior, source, predictions)
    out = tmp_path / "out"
    out.mkdir()
    client.output_dir = out
    calls = []
    def replacement(self, model, prompt, schema, max_output):
        calls.append(prompt)
        assert "VALIDATION_ERROR_FOR_CURRENT_MEETING_ONLY" in prompt
        assert "p-145d7d661374aae6" in prompt and "p-752e663f76945fc4" in prompt
        assert not any(other in prompt for other in ("ES2003b", "ES2003c", "ES2003d"))
        self.counter.reserve(model, {"prompt": prompt}, max_output)
        return payload("linkloom", 2)  # Still invalid: both rows match g1.
    monkeypatch.setattr(final.ev.Client, "generate", replacement)
    with pytest.raises(ValueError, match="Invalid or many-to-one match"):
        final.ev.finish_evaluation([meeting], gold_path, out, client,
            {mid: {"baseline": [pred], "linkloom": [pred, pred]}})
    assert len(calls) == 1 and client.position == 2
    assert client.counter.requests == 29
    assert client.counter.usd_upper_bound > 0.2
    assert (out / "ES2003a.linkloom.judge.json").is_file()  # Invalid raw decision is retained.
    with pytest.raises(RuntimeError, match="cannot call an evaluated model"):
        client.complete(None)
    with pytest.raises(RuntimeError, match="cannot initialize Gemini"):
        client.gemini_adapter()


def test_continuation_rejects_changed_predictions_before_provider_call(tmp_path, monkeypatch):
    client = final.JudgeContinuationClient({"counter": {"requests": 28, "reserved_upper_bound_usd": 0.2},
        "actual_cost_usd_from_usage": 0.2, "usage_by_model": {}}, tmp_path,
        {"ES2003a": {"baseline": []}})
    def forbidden(*args, **kwargs):
        pytest.fail("No HTTP request allowed for changed predictions")
    monkeypatch.setattr(final.ev.Client, "generate", forbidden)
    prompt = '\nDATA:\n' + json.dumps({"meeting": {"meeting_id": "ES2003a"},
        "predictions": [{"prediction_id": "different", "task": "Changed"}]})
    with pytest.raises(ValueError, match="differs from the frozen scoring protocol"):
        client.generate(final.ev.DEEPSEEK, prompt, final.ev.REVIEW_JUDGE_SCHEMA, 32768)
    assert client.counter.requests == 28

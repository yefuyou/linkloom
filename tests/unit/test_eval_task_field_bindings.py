"""Offline integrity checks for the fixed development comparison harness."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "eval_task_field_bindings.py"
_SPEC = importlib.util.spec_from_file_location("task_field_sample_eval", _PATH)
ev = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ev
_SPEC.loader.exec_module(ev)

PRICES = dict(input_per_million=.75, cached_input_per_million=.075, output_per_million=3.75,
    checked="2026-11-01", source="https://ai.google.dev/gemini-api/docs/pricing", basis="API usage, not account invoice")


def sources():
    result = {}
    for mid in ev.MEETINGS:
        utterances, text = [], ""
        for i in range(65):
            body = f"Example item {i} in {mid}."
            prefix = f"A [{float(i):.3f}-{float(i + 1):.3f}]: "
            line = prefix + body + "\n"
            utterances.append(dict(char_start=len(text), char_end=len(text) + len(line),
                text=body, speaker="A", start=float(i), end=float(i + 1), segment_id=f"{mid}-{i}"))
            text += line
        result[mid] = dict(meeting_id=mid, text=text, utterances=utterances,
            speakers=[dict(speaker="A", participant_id="person-a", role="Designer")])
    return result


def references(meetings):
    source = meetings["ES2002a"]
    quote = source["text"][source["utterances"][0]["char_start"]:source["utterances"][0]["char_end"]]
    return [dict(gold_id=f"g{i}", meeting_id="ES2002a", quote=quote,
        task=f"Example task {i}", owner="A", deadline="未说明") for i in range(11)]


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    meetings = sources()
    for mid, value in meetings.items():
        ev.write_new(data / "transcripts" / (mid + ".json"), value)
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text("\n".join(json.dumps(row) for row in references(meetings)), encoding="utf-8")
    def snapshot(directory):
        directory = Path(directory)
        ev.write_new(directory / "snapshot.json", dict(commit=ev.BASELINE, files={}))
    monkeypatch.setattr(ev, "freeze_baseline", snapshot)
    ev.prepare(data, gold_path, out)
    path = out / "negative-review.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row["non_task"] = "true"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return out


def wire(budget):
    budget.wire(SimpleNamespace(method="POST", url=SimpleNamespace(
        host="generativelanguage.googleapis.com", path="/v1/models/example:generateContent")))


def settle_one(budget, path, variant="old", sample_id="sample-1"):
    budget.reserve(variant, sample_id, ev.MODEL, {"contents": "x"}, 100)
    wire(budget)
    budget.settle(dict(prompt_token_count=10, candidates_token_count=2, thoughts_token_count=3,
        cached_content_token_count=4))
    ev.write_new(path, {"prediction": "known"})
    budget.finish(path)


def test_default_and_worker_need_explicit_live(capsys):
    ev.main([])
    assert json.loads(capsys.readouterr().out)["network"] == "not used"
    for command in (["run", "--out", "absent", "--price-checked", "today"],
            ["_worker", "--out", "absent", "--variant", "old", "--pricing", "{}"]):
        with pytest.raises(SystemExit) as caught:
            ev.main(command)
        assert caught.value.code == 2


def test_manifest_is_deterministic_and_gold_quotes_never_negative():
    meetings = sources()
    gold = references(meetings)
    # One reference spanning two canonical utterances must retain both.
    gold[0]["quote"] = meetings["ES2002a"]["text"][:meetings["ES2002a"]["utterances"][1]["char_end"]]
    first = ev.build_manifest(meetings, gold)
    assert first == ev.build_manifest(meetings, gold)
    assert first["positive_utterances"] == 2 and first["sample_count"] == 202
    assert first["generation"]["max_output_tokens"] == 4096
    ids = [item["sample_id"] for item in first["samples"]]
    assert len(ids) == len(set(ids))
    positive = {item["sample_id"] for item in first["samples"] if item["gold_ids"]}
    negative = {item["sample_id"] for item in first["samples"] if not item["gold_ids"]}
    assert len(negative) == 200 and not positive & negative
    assert set(first["gold_to_samples"]["g0"]) == positive


def test_original_or_ambiguous_gold_evidence_is_rejected():
    meetings = sources()
    gold = references(meetings)
    gold[0]["quote"] = "invented"
    with pytest.raises(ValueError, match="exactly once"):
        ev.build_manifest(meetings, gold)
    meetings["ES2002a"]["utterances"][0]["text"] = "edited"
    with pytest.raises(ValueError, match="unchanged original"):
        ev.utterance_inventory(meetings)


def test_loader_opens_only_literal_es2002_files(tmp_path):
    for mid, value in sources().items():
        ev.write_new(tmp_path / "transcripts" / (mid + ".json"), value)
    gold = tmp_path / "gold.jsonl"
    rows = references(sources())
    gold.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    (tmp_path / "transcripts" / "ES2003a.json").write_text("invalid json never read", encoding="utf-8")
    assert tuple(ev.load_development(tmp_path, gold)[0]) == ev.MEETINGS
    rows[0]["meeting_id"] = "ES2003a"
    gold.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    with pytest.raises(ValueError, match="Only ES2002"):
        ev.load_development(tmp_path, gold)


def test_preflight_requires_unchanged_negative_review_and_manifest(prepared):
    manifest, _, protocol = ev.verify_inputs(prepared)
    assert manifest["sample_count"] == 201 and protocol["manifest_sha256"]
    path = prepared / "negative-review.csv"
    original = path.read_text(encoding="utf-8-sig")
    path.write_text(original.replace("true", "false", 1), encoding="utf-8-sig")
    with pytest.raises(ValueError, match="non_task=true"):
        ev.verify_inputs(prepared)
    path.write_text(original, encoding="utf-8-sig")
    manifest["seed"] += 1
    ev.replace_json(prepared / "manifest.json", manifest)
    with pytest.raises(ValueError, match="protocol mismatch"):
        ev.verify_inputs(prepared)


def test_budget_durable_reservation_precedes_wire_and_usage_includes_thinking(tmp_path):
    path = tmp_path / "budget.json"
    budget = ev.DurableBudget(path, {"fixed": 1}, 2, PRICES)
    with pytest.raises(RuntimeError, match="No durable"):
        wire(budget)
    settle_one(budget, tmp_path / "prediction.json")
    state = ev.load_json(path)
    expected = ((10 - 4) * .75 + 4 * .075 + (2 + 3) * 3.75) / 1e6
    assert state["requests"] == state["actual_wire_requests"] == 1
    assert state["known_usage_cost_usd"] == pytest.approx(expected)
    assert state["charged_upper_bound_usd"] == pytest.approx(expected)
    reopened = ev.DurableBudget(path, {"fixed": 1}, 2, PRICES)
    assert reopened.state == state
    with pytest.raises(ValueError, match="protocol changed"):
        ev.DurableBudget(path, {"fixed": 2}, 2, PRICES)


def test_unknown_failure_or_second_wire_never_replayed(tmp_path):
    path = tmp_path / "budget.json"
    budget = ev.DurableBudget(path, {}, 2, PRICES)
    budget.reserve("old", "s", ev.MODEL, {}, 100)
    wire(budget)
    with pytest.raises(RuntimeError, match="repeated"):
        wire(budget)
    budget.fail(TimeoutError())
    assert budget.state["charged_upper_bound_usd"] > 0
    with pytest.raises(ValueError, match="no automatic replay"):
        ev.DurableBudget(path, {}, 2, PRICES)


def test_both_request_and_dollar_limits_stop_before_reserve(tmp_path):
    budget = ev.DurableBudget(tmp_path / "budget.json", {}, 1, PRICES)
    with pytest.raises(RuntimeError, match="cost ceiling"):
        budget.reserve("old", "s", ev.MODEL, {"large": "x" * 2_000_000}, 4096)
    assert budget.state["requests"] == 0 and budget.state["active"] is None
    settle_one(budget, tmp_path / "p.json")
    with pytest.raises(RuntimeError, match="ceiling"):
        budget.reserve("new", "s", ev.MODEL, {}, 100)
    assert budget.state["requests"] == 1


def test_usage_missing_or_exceeds_reservation_stops_with_charge_retained(tmp_path):
    budget = ev.DurableBudget(tmp_path / "budget.json", {}, 1, PRICES)
    budget.reserve("old", "s", ev.MODEL, {}, 10)
    wire(budget)
    before = budget.state["charged_upper_bound_usd"]
    for usage in ({}, dict(prompt_token_count=100_000, candidates_token_count=1)):
        with pytest.raises(ValueError):
            budget.settle(usage)
    assert budget.state["charged_upper_bound_usd"] == before


def test_sanitized_responses_drop_hidden_thoughts_and_signatures():
    raw = {"candidates": [{"finish_reason": "STOP", "content": {"parts": [
        {"thought": True, "text": "secret reasoning"},
        {"function_call": {"name": "tool", "args": {}}, "thought_signature": "encrypted"}]}}]}
    assert "secret reasoning" not in json.dumps(ev.sanitize_response(raw))
    assert "encrypted" not in json.dumps(ev.sanitize_response(raw))


def test_child_command_preserves_live_opt_in_and_source_isolation(prepared, monkeypatch):
    calls = []
    monkeypatch.setattr(ev, "source_signature", lambda: {"stable": "hash"})
    monkeypatch.setattr(ev.subprocess, "run", lambda command, **kwargs:
        calls.append((command, kwargs)) or SimpleNamespace(returncode=0))
    ev.run(prepared, "both", PRICES)
    assert len(calls) == 2
    for (command, options), variant in zip(calls, ("old", "new")):
        assert "--live" in command
        assert command[command.index("--variant") + 1] == variant
        assert options["env"]["PYTHONPATH"].split(ev.os.pathsep)[0].endswith(
            str(Path("baseline/src")) if variant == "old" else str(Path("src")))
    with pytest.raises(ValueError, match="--resume"):
        ev.run(prepared, "both", PRICES)
    ev.run(prepared, "both", PRICES, resume=True)


def complete_fake_run(out, monkeypatch):
    monkeypatch.setattr(ev, "source_signature", lambda: {"stable": "hash"})
    manifest, _, protocol = ev.verify_inputs(out)
    ev.write_new(out / "run-protocol.json", dict(protocol=protocol, pricing=PRICES,
        source_files=ev.source_signature()))
    budget = ev.DurableBudget(out / "budget.json", dict(protocol, current_source_sha256=ev.digest(ev.source_signature())),
        manifest["max_requests"], PRICES)
    for variant in ("old", "new"):
        for item in manifest["samples"]:
            path = out / "predictions" / variant / (item["sample_id"] + ".json")
            budget.reserve(variant, item["sample_id"], ev.MODEL, {}, 100)
            wire(budget)
            budget.settle(dict(prompt_token_count=1, candidates_token_count=1))
            ev.write_new(path, dict(variant=variant, sample_id=item["sample_id"], candidates=[]))
            budget.finish(path)
    return manifest


def test_final_integrity_rejects_prediction_budget_and_source_tampering(prepared, monkeypatch):
    manifest = complete_fake_run(prepared, monkeypatch)
    ev.verify_completed_run(prepared, manifest)
    path = prepared / "predictions" / "old" / (manifest["samples"][0]["sample_id"] + ".json")
    original = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="prediction was changed"):
        ev.judgment_template(prepared)
    path.write_bytes(original)
    state = ev.load_json(prepared / "budget.json")
    state["actual_wire_requests"] -= 1
    ev.replace_json(prepared / "budget.json", state)
    with pytest.raises(ValueError, match="prefix"):
        ev.verify_completed_run(prepared, manifest)
    state["actual_wire_requests"] += 1
    ev.replace_json(prepared / "budget.json", state)
    monkeypatch.setattr(ev, "source_signature", lambda: {"changed": "hash"})
    with pytest.raises(ValueError, match="source/protocol changed"):
        ev.verify_completed_run(prepared, manifest)


def test_offline_preview_uses_guard_and_only_one_selected_message(prepared, monkeypatch):
    import linkloom.semantic_ingestion.extraction as extraction
    # This test uses a one-message fixture while retaining the real production
    # extractor/prompt path. No SDK object or network client is constructed.
    meeting = sources()["ES2002a"]
    sample = dict(sample_id="ES2002a-0001", meeting_id="ES2002a", ordinal=1,
        speaker="A", text=meeting["utterances"][1]["text"])
    protocol = {"fixed": "offline"}
    manifest = {"samples": [sample]}
    monkeypatch.setattr(ev, "verify_inputs", lambda out: (manifest, {"ES2002a": meeting}, protocol))
    monkeypatch.setattr(ev, "MEETINGS", ("ES2002a",))
    # New mode imports the active checkout; the preview's exact import-path
    # check still runs, and the real provider-facing extractor guard is denied.
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-read-or-sent")
    result = ev.preview_worker(prepared, "new")
    assert result["samples"] == 1 and result["network"].startswith("not used")
    request = ev.load_json(prepared / "request-preview" / "new.json")[0]["request"]
    assert sample["text"] in request["user_input"]
    assert meeting["utterances"][0]["text"] not in request["user_input"]
    assert "must-not-be-read-or-sent" not in ev.canonical(request)
    assert request["generation_options"]["max_output_tokens"] == 4096

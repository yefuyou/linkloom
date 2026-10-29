"""Offline harness checks; tiny fixtures are NOT AMI evaluation data."""
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest


_PATH = Path(__file__).resolve().parents[2] / "scripts" / "eval_tasks.py"
_SPEC = importlib.util.spec_from_file_location("ami_eval_tasks", _PATH)
ev = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ev
_SPEC.loader.exec_module(ev)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must never reach HTTP")
    monkeypatch.setattr(ev.urllib.request, "urlopen", blocked)


def meeting(mid="ES2002a", sentences=None):
    lines = sentences or ["A [0.000-1.000]: I will send the report.\n"]
    utterances, offset = [], 0
    for line in lines:
        utterances.append({"char_start": offset, "char_end": offset + len(line)})
        offset += len(line)
    return {"meeting_id": mid, "text": "".join(lines), "utterances": utterances,
            "speakers": [{"speaker": "A", "role": "Project Manager", "participant_id": "test-person"}]}


def task():
    return {"quote": "I will send the report.", "task": "send the report", "owner": "A", "deadline": ev.UNKNOWN}


def judgment(pid="p1", gid="g1", category="MATCH", **updates):
    return {"prediction_id": pid, "gold_id": gid, "category": category,
        "owner_correct": category == "MATCH", "deadline_correct": category == "MATCH",
        "reason": "Same committed deliverable", "evidence_quote": "I will send the report.", **updates}


def test_default_and_nonlive_commands_never_call_models(capsys):
    ev.main([])
    assert json.loads(capsys.readouterr().out)["network"] == "not used by plan"
    with pytest.raises(RuntimeError, match="--live"):
        ev.Client(ev.Counter(30, .5)).generate(ev.DEEPSEEK, "x", {})
    with pytest.raises(SystemExit) as error:
        ev.main(["evaluate", "--data", "absent", "--out", "absent", "--gold", "absent"])
    assert error.value.code == 2


def test_both_counter_limits_stop_before_next_request():
    counter = ev.Counter(1, .5)
    counter.reserve(ev.DEEPSEEK, {"text": "x"}, 100)
    with pytest.raises(RuntimeError, match="ceiling"):
        counter.reserve(ev.DEEPSEEK, {"text": "x"}, 100)
    assert counter.requests == 1
    counter = ev.Counter(200, .00001)
    with pytest.raises(RuntimeError, match="ceiling"):
        counter.reserve(ev.DEEPSEEK, {"text": "x"}, 100)
    assert counter.requests == 0 and counter.usd_upper_bound == 0


def test_usage_settlement_releases_only_known_unused_reservation():
    counter = ev.Counter(10, .2)
    reserved = counter.reserve(ev.DEEPSEEK, {"text": "x"}, 1000)
    counter.settle(reserved, .0001)
    assert counter.requests == 1 and counter.usd_upper_bound == pytest.approx(.0001)
    with pytest.raises(ValueError, match="exceeds"):
        counter.settle(.01, .02)


def test_gemini_settlement_includes_thought_tokens():
    from types import SimpleNamespace
    response = {"usage_metadata": {"prompt_token_count": 1000, "candidates_token_count": 100,
                                   "thoughts_token_count": 500}}
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: response))
    counter, recorded = ev.Counter(200, 2), []
    transport = ev.CountedGeminiTransport(sdk, counter, recorded.append)
    assert transport.generate_content(model=ev.GEMINI, contents=[], config={"max_output_tokens":32768}) == response
    expected = (1000 * .75 + 600 * 3.75) / 1e6
    assert counter.usd_upper_bound == pytest.approx(expected)
    assert recorded[0]["cost_usd_from_usage"] == pytest.approx(expected)


def test_failed_http_is_counted_once_without_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key")
    calls = []
    def fail(request, **kwargs):
        body = json.loads(request.data)
        assert body["thinking"] == {"type": "disabled"}
        calls.append(request)
        raise ev.urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, None)
    monkeypatch.setattr(ev.urllib.request, "urlopen", fail)
    counter = ev.Counter(30, .5)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        ev.Client(counter, live=True).generate(ev.DEEPSEEK, "test", {})
    assert counter.requests == len(calls) == 1
    assert counter.usd_upper_bound > 0


def test_chunks_preserve_every_character_and_utterance_boundaries():
    source = meeting(sentences=[f"A: sentence {i}\n" for i in range(20)])
    parts = ev.chunks(source, max_chars=85, max_bytes=85)
    covered = set()
    for start, text in parts:
        assert source["text"][start:start + len(text)] == text
        assert start in {u["char_start"] for u in source["utterances"]}
        assert len(text) <= 85
        covered.update(range(start, start + len(text)))
    assert covered == set(range(len(source["text"])))
    with pytest.raises(ValueError, match="utterance"):
        ev.chunks(meeting(sentences=["x" * 100]), max_chars=10)


def test_xml_ranges_keep_speaker_order_and_fail_on_uncovered_words():
    def archive_bytes(complete):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for index, speaker in enumerate("ABCD"):
                words = (f'<root xmlns:nite="http://nite.sourceforge.net/">'
                    f'<w nite:id="{speaker}1" starttime="{index}" endtime="{index + .1}">Send</w>'
                    f'<vocalsound nite:id="{speaker}v"/>'
                    f'<w nite:id="{speaker}2" starttime="{index + .2}" endtime="{index + .3}">report.</w></root>')
                end = f"..id({speaker}2)" if complete else ""
                segments = (f'<root xmlns:nite="http://nite.sourceforge.net/">'
                    f'<segment nite:id="s{speaker}"><nite:child href="words.xml#id({speaker}1){end}"/></segment></root>')
                archive.writestr(f"words/ES2002a.{speaker}.words.xml", words)
                archive.writestr(f"segments/ES2002a.{speaker}.segments.xml", segments)
        buffer.seek(0)
        return buffer
    with zipfile.ZipFile(archive_bytes(True)) as archive:
        result = ev.transcript_from_archive(archive, "ES2002a", [])
    assert [u["speaker"] for u in result["utterances"]] == list("ABCD")
    assert result["text"].startswith("A [0.000-0.300]: Send report.")
    assert result["utterances"][0]["word_ids"] == ["A1", "A2"]
    with zipfile.ZipFile(archive_bytes(False)) as archive, pytest.raises(ValueError, match="Uncovered"):
        ev.transcript_from_archive(archive, "ES2002a", [])


def test_summary_empty_action_is_not_gold_and_ids_survive():
    root = ET.fromstring('<root xmlns:nite="http://nite.sourceforge.net/"><actions>'
        '<sentence nite:id="s1">*NA*</sentence><sentence nite:id="s2">Send report</sentence>'
        '</actions><decisions><sentence nite:id="s3">Use blue</sentence></decisions></root>')
    assert ev.summary_actions(root) == [{"id": "s2", "text": "Send report"}]


def test_exact_quotes_and_action_references_are_enforced():
    source = meeting()
    assert ev.validate_tasks({"tasks": [task()]}, source) == [task()]
    for bad in ({**task(), "quote": "I will submit the report."}, {**task(), "owner": ""}):
        with pytest.raises(ValueError):
            ev.validate_tasks({"tasks": [bad]}, source)
    with pytest.raises(ValueError, match="AMI action"):
        ev.validate_tasks({"tasks": [{**task(), "ami_action_id": "invented"}]}, source, {})


def test_existing_linkloom_discovery_runs_with_fake_transport():
    class FakeClient:
        counter = ev.Counter(200, 2)
        def complete(self, request):
            from linkloom.agents.model_adapter import ModelAction, ModelResponse
            from linkloom.tools.contracts import ToolCall
            assert request.model_id == ev.GEMINI and "WORK DISCOVERY CONTRACT" in request.user_input
            schema = request.available_tools[0].input_schema
            assert schema["properties"]["claims"]["maxItems"] == 20
            assert request.generation_options.max_output_tokens == 32768
            assert request.generation_options.thinking_level == "low"
            assert request.generation_options.temperature is None
            witness = "send the report"
            bindings = [{"field": field, "value": None, "role": "UNKNOWN", "state": "NOT_STATED",
                "quote": "", "task_quote": witness, "basis": "SOURCE_TEXT", "message_ordinal": 0}
                for field in ("owner", "deadline", "project", "vendor")]
            payload = {"claims": [{"claim_type": "ACTION", "subject": None, "relation": None, "value": None,
                "temporal_status": "NOT_STATED", "valid_from": None, "valid_to": None,
                "confidence": {k: .9 for k in ("claim_type", "entity", "relation", "temporal", "overall")},
                "task": {"text": "I will send the report.", "description_parts": [witness],
                    "owner": None, "deadline_text": None, "project": None, "vendor": None,
                    "field_bindings": bindings, "work_state": "ASSERTED"}}]}
            return ModelResponse(action=ModelAction.tool(ToolCall(call_id="offline", tool_id=request.available_tools[0].tool_id,
                arguments=payload)))
    predictions, diagnostics = ev.linkloom_predict(meeting(), FakeClient())
    assert len(predictions) == 1
    assert "send the report" in predictions[0]["task"]
    assert predictions[0]["owner"] == ev.UNKNOWN
    assert diagnostics[0]["failures"] == []


def test_baseline_only_receives_transcript_not_labels_or_internal_xml_ids():
    class FakeClient:
        def generate(self, model, prompt, schema, max_output):
            assert "PRIVATE_LABEL" not in prompt
            assert "PRIVATE_WORD_ID" not in prompt
            assert "participant_id" not in prompt  # Same textual input boundary as LinkLoom.
            assert model == ev.GEMINI
            return {"tasks": [task()]}
    source = {**meeting(), "labels": "PRIVATE_LABEL", "word_ids": ["PRIVATE_WORD_ID"]}
    ev.baseline_predict(source, FakeClient())


def test_one_to_one_judgments_and_zero_denominator_metrics():
    preds = [{"prediction_id": "p1", "deadline": ev.UNKNOWN}, {"prediction_id": "p2", "deadline": "无法确定"}]
    gold = [{"gold_id": "g1", "owner": "A", "deadline": ev.UNKNOWN}]
    duplicate = judgment("p2", None, "DUPLICATE")
    matched = judgment(deadline_correct=False)
    rows = ev.validate_judgments({"judgments": [matched, duplicate]}, preds, gold, meeting()["text"])
    result = ev.metrics(rows, gold)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 0)
    assert result["precision"] == .5 and result["recall"] == 1
    assert result["owner_stated_matched_count"] == 1
    assert result["deadline_accuracy_on_matched_stated_gold"] is None
    assert result["deadline_stated_gold_count"] == 0
    assert result["deadline_prediction_abstentions"] == 2
    assert ev.metrics([], [*gold])["recall"] == 0
    assert ev.metrics([], [])["precision"] is None
    for bad_rows in ([matched, judgment("p2", deadline_correct=False)], [matched],
                     [judgment(deadline_correct=False, evidence_quote="fabricated"), duplicate],
                     [matched, {**duplicate, "owner_correct": True}], [judgment(), duplicate]):
        with pytest.raises(ValueError):
            ev.validate_judgments({"judgments": bad_rows}, preds, gold, meeting()["text"])


def test_only_explicit_gold_deadlines_are_scored_abstention_is_not_correct():
    gold = [{"gold_id": "g1", "owner": "A", "deadline": "next meeting"},
            {"gold_id": "g2", "owner": "A", "deadline": ev.UNKNOWN},
            {"gold_id": "g3", "owner": "A", "deadline": "Friday"}]
    rows = [{**judgment(deadline_correct=False), "predicted_deadline": "无法确定"},
            {**judgment("p2", "g2", deadline_correct=False), "predicted_deadline": ev.UNKNOWN}]
    result = ev.metrics(rows, gold)
    assert result["deadline_stated_gold_count"] == 2
    assert result["deadline_stated_matched_count"] == 1
    assert result["deadline_accuracy_on_matched_stated_gold"] == 0
    assert result["deadline_end_to_end_accuracy_on_stated_gold"] == 0
    assert result["deadline_abstentions_on_matched_stated_gold"] == 1
    assert "associated" in ev.MATCH_RULES and "role" in ev.MATCH_RULES


def test_preannotation_uses_exact_source_ranges_and_separates_translations():
    source = meeting()
    source["utterances"][0]["segment_id"] = "original-segment"
    proposal = {"quote_start_segment_id": "original-segment", "quote_end_segment_id": "original-segment",
        "task": "send the report", "owner": "Project Manager", "deadline": ev.UNKNOWN,
        "ami_action_id": "", "quote_zh": "我会发送报告。", "task_zh": "发送报告"}
    rows = ev.resolve_preannotation({"tasks": [proposal]}, source, {})
    assert rows[0]["quote"] == source["text"]
    assert source["text"][rows[0]["quote_start"]:rows[0]["quote_end"]] == rows[0]["quote"]
    assert rows[0]["引用原文的中文翻译"] == "我会发送报告。"
    with pytest.raises(ValueError, match="unknown/reversed"):
        ev.resolve_preannotation({"tasks": [{**proposal, "quote_start_segment_id": "invented"}]}, source, {})


def test_actual_deepseek_cost_uses_cache_counts_and_time_not_reservation():
    result = ev.deepseek_usage({"model": ev.DEEPSEEK, "created": int(ev.datetime(2026, 10, 29, 5, tzinfo=ev.UTC).timestamp()),
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                  "prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 900}})
    assert result["pricing_period"] == "off_peak"
    assert result["cost_usd_from_usage"] == pytest.approx((100 * .003 + 900 * .15 + 500 * .6) / 1e6)


def test_official_metadata_maps_nxt_agent_to_human_readable_role():
    xml = '<root><meeting observation="ES2002a">' + ''.join(
        f'<speaker nxt_agent="{speaker}" global_name="person-{speaker}" role="{role}"/>'
        for speaker, role in zip("ABCD", ("ID", "PM", "UI", "ME"))) + '</meeting></root>'
    speakers = ev.speaker_metadata(ET.fromstring(xml), "ES2002a")
    assert speakers[1] == {"speaker": "B", "participant_id": "person-B", "role": "Project Manager"}


def test_full_quote_translation_is_deduplicated_without_changing_english():
    class FakeClient:
        last_call = {"usage": "offline"}
        def generate(self, model, prompt, schema, max_output):
            assert model == ev.DEEPSEEK and max_output == 6144
            assert "EVERY sentence" in prompt
            assert prompt.count('"quote_id":') == 1
            return {"translations": [{"quote_id": "q000", "quote_zh": "我会发送报告。还有其他讨论。"}]}
    rows = [{"quote": "I will send the report. Other discussion.", "task": "Send report", "owner": "Project Manager"}] * 2
    rows = [dict(row) for row in rows]
    ev.translate_review_quotes(rows, FakeClient())
    assert rows[0]["引用原文的中文翻译"] == rows[1]["引用原文的中文翻译"]
    assert rows[0]["task"] == "Send report" and rows[0]["owner"] == "Project Manager"


def test_four_meetings_are_active_and_second_team_is_only_planned():
    assert ev.MEETINGS == ("ES2002a", "ES2002b", "ES2002c", "ES2002d")
    assert ev.PLANNED_MEETINGS == ("ES2003a", "ES2003b", "ES2003c", "ES2003d")


def test_both_systems_use_actual_product_model_and_low_thinking():
    from linkloom.agents.providers.gemini_api import GEMINI_38_FLASH_MODEL_ID, GeminiProviderAdapter
    class FakeGemini:
        def generate_content(self, *, model, contents, config):
            assert model == ev.GEMINI == GEMINI_38_FLASH_MODEL_ID
            assert config["thinking_config"] == {"thinking_level": "LOW"}
            assert config["max_output_tokens"] == 32768
            assert "temperature" not in config
            function = config["tools"][0]["function_declarations"][0]["name"]
            return {"candidates": [{"finish_reason": "STOP", "content": {"parts": [
                {"function_call": {"name": function, "args": {"tasks": [task()]}}}]}}]}
    client = ev.Client(ev.Counter(200, 2), live=True)
    client._gemini = GeminiProviderAdapter(FakeGemini(), model_id=ev.GEMINI)
    # Whole-meeting baseline must preserve inputs larger than the product's 32 KiB limit.
    assert ev.baseline_predict(meeting(sentences=["A: context.\n" * 4000, meeting()["text"]]), client) == [task()]


def test_gold_corrections_do_not_use_notes_or_rejected_candidates(tmp_path):
    path = tmp_path / "gold.csv"
    row = dict.fromkeys(ev.CSV_FIELDS, "")
    row.update(task(), candidate_id="g1", meeting_id="ES2002a", keep="y", 修改后的任务="Submit final report", 修改后的负责人=ev.UNKNOWN)
    rejected = {**row, "candidate_id": "g2", "keep": "n", "task": "PRIVATE_REJECTED", "备注": "PRIVATE_NOTE"}
    ev.new_csv(path, ev.CSV_FIELDS, [row, rejected])
    result = ev.load_gold(path, [meeting()])["ES2002a"]
    assert len(result) == 1 and result[0]["task"] == "Submit final report"
    assert result[0]["owner"] == ev.UNKNOWN and "PRIVATE" not in json.dumps(result)


def test_evaluate_opens_gold_only_after_both_predictions_and_audits_fn(tmp_path, monkeypatch):
    source = meeting()
    opened, predicted = [], []
    monkeypatch.setattr(ev, "load_meetings", lambda data: [source])
    def predict(meeting, client):
        assert not opened
        predicted.append(True)
        return []
    monkeypatch.setattr(ev, "baseline_predict", predict)
    monkeypatch.setattr(ev, "linkloom_predict", lambda m, c: (predict(m, c), []))
    def gold(path, meetings):
        assert len(predicted) == 2
        assert (tmp_path / "result" / "predictions.json").exists()
        opened.append(True)
        return {"ES2002a": [{"gold_id": "g1", **task()}]}
    monkeypatch.setattr(ev, "load_gold", gold)
    class FakeClient:
        counter = ev.Counter(200, 2)
        def generate(self, *args):
            raise AssertionError("No predictions means no judge call")
    ev.evaluate(tmp_path, tmp_path / "unopened.csv", tmp_path / "result", FakeClient())
    report = json.loads((tmp_path / "result" / "results.json").read_text(encoding="utf-8"))
    assert report["micro"]["baseline"]["fn"] == 1
    with (tmp_path / "result" / "audit_20pct.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and all(r["category"] == "FN" for r in rows)


def test_audit_sample_is_reproducible_and_twenty_percent_per_system():
    rows = [{"system": system, "meeting_id": "ES2002a", "prediction_id": f"p{i}", "gold_id": None}
            for system in ("baseline", "linkloom") for i in range(11)]
    sample = ev.audit_sample(rows)
    assert sample == ev.audit_sample(rows)
    assert all(sum(r["system"] == system for r in sample) == 3 for system in ("baseline", "linkloom"))


def test_outputs_never_overwrite_existing_files(tmp_path):
    path = tmp_path / "kept.json"
    ev.new_json(path, {"original": True})
    with pytest.raises(FileExistsError):
        ev.new_json(path, {"original": False})
    assert json.loads(path.read_text()) == {"original": True}

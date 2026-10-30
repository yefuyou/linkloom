"""Deterministic protocol tests, never live-provider performance claims."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelTurnRequest, ModelGenerationOptions
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter, build_gemini_request, _AdapterFailure
from linkloom.runtime.errors import ValidationError
from linkloom.semantic_ingestion.artifacts import RawArtifact, evidence_ref_for, segment_id_for
from linkloom.semantic_ingestion.meeting_tasks_v2 import (
    Deadline, MeetingTaskExtractor, PROMPT, SCHEMA, TaskExtractionConfig, TOOL_ID,
    canonical, deadline_denotes_time, merge_candidates, validate_candidate,
)
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
from linkloom.tools.contracts import ToolCall


ROSTER = [{"speaker": "X", "participant_id": "person-x", "role": "Coordinator"},
          {"speaker": "Y", "participant_id": "person-y", "role": "Engineer"}]


def source(bodies=None):
    bodies = bodies or [("X", "I will send the package."), ("Y", "Please do it before the next workshop.")]
    return RawArtifact("fixture", "source-one", "timestamped_text", "\n".join(
        f"[2034-01-02T09:{i // 60:02}:{i % 60:02}Z] {speaker}: {body}" for i, (speaker, body) in enumerate(bodies)),
        ingestion_time=datetime(2034, 1, 2, tzinfo=UTC))


def task(**changes):
    return {"task": "Send the package", "owner_ids": ["X"],
        "deadline": {"raw_text": "before the next workshop", "kind": "EVENT_BASED"},
        "adopted": True, "scope": "AFTER_MEETING", "end_state": "PENDING",
        "deliverable_key": "package delivery", "reason": "Explicit undertaking and linked delivery requirement.",
        "evidence": [{"message_ordinal": 0, "supports": ["work", "adoption", "owner"]},
                     {"message_ordinal": 1, "supports": ["deadline"]}], **changes}


def validate(row, artifact=None, allowed=None):
    artifact = artifact or source()
    segments = parse_timestamped_text(artifact).segments
    return validate_candidate(row, artifact, segments, {s["speaker"]: s for s in ROSTER},
                              allowed if allowed is not None else {s.ordinal for s in segments})


class Adapter:
    def __init__(self, rows=None, callback=None):
        self.rows, self.callback, self.requests = rows or [], callback, []

    def complete(self, request):
        self.requests.append(request)
        if self.callback:
            return self.callback(request)
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id="offline", tool_id=TOOL_ID,
            arguments={"coverage_complete": True, "tasks": deepcopy(self.rows)})))


def extractor(adapter, **kwargs):
    return MeetingTaskExtractor(adapter, model_id="gemini-3.8-flash",
        config=kwargs.pop("config", TaskExtractionConfig(task_extraction_mode="meeting_v2")),
        request_guard=kwargs.pop("request_guard", lambda request: True), **kwargs)


def test_default_is_legacy_and_unknown_modes_fail_closed():
    assert TaskExtractionConfig().task_extraction_mode == "legacy"
    with pytest.raises(ValueError, match="explicit"):
        extractor(Adapter(), config=TaskExtractionConfig())
    with pytest.raises(ValueError):
        TaskExtractionConfig(task_extraction_mode="silent-fallback")


def test_one_call_includes_full_meeting_fields_status_and_all_source_witnesses():
    adapter = Adapter([task()]); artifact = source()
    result = extractor(adapter).extract(artifact, ROSTER)
    assert len(adapter.requests) == 1 and len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.owner_ids == ("person-x",) and candidate.verify(artifact)
    assert candidate.deadline == Deadline("before the next workshop", "EVENT_BASED")
    assert candidate.scope == "AFTER_MEETING" and candidate.end_state == "PENDING"
    assert [e.span.ordinal for e in candidate.evidence] == [0, 1]
    assert result.coverage_complete and not result.rejected and not result.excluded
    assert json.loads(adapter.requests[0].user_input.split("\nDATA:\n")[1])["whole_meeting"] is True
    assert adapter.requests[0].generation_options.thinking_level == "low"
    assert adapter.requests[0].generation_options.max_output_tokens == 32768


def test_full_meeting_is_preferred_even_above_fallback_body_target():
    artifact = source([("X", "Text. " * 5000)])
    adapter = Adapter()
    result = extractor(adapter, config=TaskExtractionConfig(task_extraction_mode="meeting_v2", window_body_bytes=1)).extract(artifact, ROSTER)
    assert len(adapter.requests) == 1 and result.windows[0]["whole_meeting"]


def test_over_limit_uses_complete_large_windows_and_reports_every_utterance():
    artifact = source([("X", f"Line {i} " + "x" * 3500) for i in range(25)])
    adapter = Adapter()
    result = extractor(adapter, config=TaskExtractionConfig(task_extraction_mode="meeting_v2", max_input_bytes=32768,
        window_body_bytes=20000)).extract(artifact, ROSTER)
    covered = set()
    assert len(adapter.requests) > 1
    for request in adapter.requests:
        assert len(canonical(request.user_input).encode()) <= 32768
        data = json.loads(request.user_input.split("\nDATA:\n")[1])
        assert data["whole_meeting"] is False
        for message in data["messages"]:
            assert len(message["text"]) >= 3500
            covered.add(message["ordinal"])
    assert covered == set(range(25)) and len(result.windows) == len(adapter.requests)


def test_call_cap_and_oversize_utterance_fail_before_any_call():
    adapter = Adapter()
    config = TaskExtractionConfig(task_extraction_mode="meeting_v2", max_input_bytes=32768, window_body_bytes=100)
    with pytest.raises(ValueError, match="complete utterance"):
        extractor(adapter, config=config).extract(source([("X", "x" * 40000)]), ROSTER)
    with pytest.raises(ValueError, match="call limit"):
        extractor(adapter, config=replace(config, window_body_bytes=20000, max_calls=1)).extract(
            source([("X", "x" * 18000) for _ in range(3)]), ROSTER)
    assert not adapter.requests


@pytest.mark.parametrize("raw,kind", [("2037-04-05", "ABSOLUTE_DATE"), ("two hours later", "RELATIVE_TIME"),
    ("下次评审之前", "EVENT_BASED"), ("as soon as possible", "RELATIVE_TIME"), (None, "UNSPECIFIED")])
def test_dates_preserve_original_wording_and_never_resolve(raw, kind):
    artifact = source([("X", "I will send the package " + (raw or "when the timing is decided") + ".")])
    row = task(deadline={"raw_text": raw, "kind": kind}, evidence=[{"message_ordinal": 0,
        "supports": ["work", "adoption", "owner", "deadline"]}])
    candidate = validate(row, artifact)
    assert candidate.deadline.raw_text == raw and candidate.deadline.kind == kind
    assert candidate.deadline.resolved_at is None
    with pytest.raises(ValueError):
        Deadline(raw, kind, "2037-01-01")


@pytest.mark.parametrize("raw", ["inside the spreadsheet", "on our dashboard", "via email",
    "by email", "by e-mail", "in the next document", "在附件里", "在报告前言中", "通过邮件", "于文件夹内"])
def test_container_and_channel_are_not_deadline_roles(raw):
    assert not deadline_denotes_time(raw)
    artifact = source([("X", "I will send the package " + raw + ".")])
    row = task(deadline={"raw_text": raw, "kind": "EVENT_BASED"}, evidence=[{
        "message_ordinal": 0, "supports": ["work", "owner", "deadline"]}])
    candidate = validate(row, artifact)
    assert candidate.is_pending and candidate.verify(artifact)
    assert candidate.deadline == Deadline(None, "UNSPECIFIED")
    assert candidate.validation_issues == ("DEADLINE_NON_TEMPORAL_CONTAINER",)


@pytest.mark.parametrize("raw", ["2037-04-05", "within three days", "before submitting the document",
    "on publication of the document", "after approval of the spreadsheet", "in two days via email",
    "before the next workshop", "as soon as possible", "在验收完成之后", "在下次评审前", "在三天内通过邮件",
    "on document submission", "on the spreadsheet submission date", "在文档发布当天", "在文件上传当日",
    "on 2037-04-05 in the document", "by the deadline"])
def test_time_points_durations_and_event_boundaries_remain_deadlines(raw):
    assert deadline_denotes_time(raw)


@pytest.mark.parametrize("change,reason", [
    ({"owner_ids": ["I"]}, "UNRESOLVED_OWNER"),
    ({"owner_ids": ["non-roster-person"]}, "UNRESOLVED_OWNER"),
    ({"owner_ids": ["X", "X"]}, "UNRESOLVED_OWNER"),
    ({"evidence": [{"message_ordinal": True, "supports": ["work"]}]}, "INVALID_EVIDENCE_HANDLE"),
    ({"evidence": [{"message_ordinal": 99, "supports": ["work"]}]}, "INVALID_EVIDENCE_HANDLE"),
    ({"evidence": [{"message_ordinal": 0, "supports": ["deadline"]}]}, "WORK_WITNESS_MISSING"),
    ({"evidence": [{"message_ordinal": 0, "supports": ["work"]}]}, "OWNER_WITNESS_MISSING"),
    ({"deadline": {"raw_text": "before the other workshop", "kind": "EVENT_BASED"}}, "DEADLINE_NOT_EXACT_SOURCE"),
])
def test_invalid_field_or_reference_is_rejected_without_repairs(change, reason):
    with pytest.raises(ValueError, match=reason):
        validate(task(**change))


def test_evidence_must_belong_to_this_window_not_just_the_same_source():
    with pytest.raises(ValueError, match="HANDLE"):
        validate(task(), allowed={0})


def test_host_exact_spans_preserve_unicode_repeated_messages_hash_and_version():
    artifact = source([("X", "完成校验🙂"), ("X", "完成校验🙂")])
    row = task(deadline={"raw_text": None, "kind": "UNSPECIFIED"}, evidence=[
        {"message_ordinal": 1, "supports": ["work", "owner"]}])
    c = validate(row, artifact); e = c.evidence[0].span
    assert e.quote == "完成校验🙂" and e.ordinal == 1
    assert artifact.content[e.char_start:e.char_end] == e.quote
    assert e.quote_sha256 == hashlib.sha256(e.quote.encode()).hexdigest()
    assert c.verify(artifact)
    assert not c.verify(replace(artifact, content=artifact.content + " changed"))
    bad_segment = segment_id_for(e.artifact_version_id,e.message_id,e.char_start+1,e.char_end+1,e.quote_sha256)
    assert not replace(e, char_start=e.char_start + 1, char_end=e.char_end + 1,
        segment_id=bad_segment, evidence_ref=evidence_ref_for(bad_segment)).verify(artifact)


@pytest.mark.parametrize("change", [{"adopted": False}, {"scope": "IN_MEETING"}, {"scope": "UNCERTAIN"},
    {"end_state": "COMPLETED"}, {"end_state": "CANCELLED"}, {"end_state": "UNCERTAIN"}])
def test_nonpending_work_is_visible_but_excluded_in_the_same_call(change):
    adapter = Adapter([task(**change)])
    batch = extractor(adapter).extract(source(), ROSTER)
    assert not batch.candidates and len(batch.excluded) == 1 and len(adapter.requests) == 1


def test_simple_merge_requires_known_same_owner_and_model_same_deliverable():
    a = validate(task())
    b = validate(task(task="Deliver the prepared package", reason="Second mention"))
    merged, = merge_candidates([a, b])
    assert set(merged.member_ids) == {a.candidate_id, b.candidate_id}
    assert len(merged.evidence) == 2 and merged.deadline.resolved_at is None
    assert len(merge_candidates([a, replace(b, owner_ids=("person-y",))])) == 2
    assert len(merge_candidates([a, replace(b, deliverable_key="different obligation")])) == 2
    unknown = replace(a, owner_ids=())
    assert len(merge_candidates([unknown, unknown])) == 2


def test_explicit_participant_ids_and_speaker_ids_resolve_to_same_trusted_principal():
    speaker = validate(task())
    participant = validate(task(owner_ids=["person-x"]))
    assert participant.owner_ids == speaker.owner_ids == ("person-x",)
    assert len(merge_candidates([speaker, participant])) == 1


def test_colliding_host_identity_alias_does_not_select_an_owner():
    artifact=source();segments=parse_timestamped_text(artifact).segments
    roster={s["speaker"]:s for s in ROSTER}
    roster["Y"]={"speaker":"Y","participant_id":"X","role":"Engineer"}
    with pytest.raises(ValueError,match="UNRESOLVED_OWNER"):
        validate_candidate(task(),artifact,segments,roster,{0,1})


def test_merge_preserves_all_nonadjacent_provenance_and_conflicting_deadline_variants():
    artifact = source([("X", "I will send the package on Monday."), ("X", "I will send the package on Tuesday.")])
    def row(ordinal, raw):
        return task(deadline={"raw_text": raw, "kind": "RELATIVE_TIME"}, evidence=[{
            "message_ordinal": ordinal, "supports": ["work", "owner", "deadline"]}])
    merged, = merge_candidates([validate(row(0, "on Monday"), artifact), validate(row(1, "on Tuesday"), artifact)])
    assert len(merged.evidence) == 2 and merged.verify(artifact)
    assert merged.deadline == Deadline(None, "UNSPECIFIED") and len(merged.deadline_variants) == 2


def test_later_completion_can_retire_a_pending_window_observation():
    artifact = source([("X", "I will send it."), ("X", "I have sent it.")])
    def row(ordinal, state):
        return task(end_state=state, deadline={"raw_text": None, "kind": "UNSPECIFIED"}, evidence=[{
            "message_ordinal": ordinal, "supports": ["work", "owner", "completion"]}])
    pending, completed = validate(row(0,"PENDING"),artifact), validate(row(1,"COMPLETED"),artifact)
    assert merge_candidates([completed,pending])[0].end_state == "COMPLETED"


def test_rejected_proposal_does_not_erase_valid_task_or_trigger_retry():
    adapter = Adapter([task(),task(owner_ids=["you"])])
    batch = extractor(adapter).extract(source(), ROSTER)
    assert len(batch.candidates) == 1 and len(batch.rejected) == 1
    assert batch.rejected[0]["reason"] == "UNRESOLVED_OWNER" and not batch.coverage_complete
    assert len(adapter.requests) == 1


def test_host_revocation_and_denied_guard_withhold_results():
    adapter = Adapter([task()]); artifact = source()
    with pytest.raises(PermissionError):
        extractor(adapter, request_guard=lambda r: False).extract(artifact, ROSTER)
    assert not adapter.requests
    current = [artifact]
    def changed(request):
        current[0] = replace(artifact,content=artifact.content+' changed')
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id='offline',tool_id=TOOL_ID,
            arguments={'coverage_complete':True,'tasks':[task()]})))
    with pytest.raises(ValueError, match="SOURCE_VERSION_CHANGED"):
        extractor(Adapter(callback=changed), current_source=lambda:current[0]).extract(artifact,ROSTER)


def request(text):
    return ModelTurnRequest(run_id="fixture",turn_id="turn",task_id="task",agent_id="agent",sequence=1,
        user_input=text,observation=None,available_tools=[],model_id="gemini-3.8-flash",
        generation_options=ModelGenerationOptions(temperature=None))


def test_legacy_request_payload_and_hash_do_not_change_with_host_default():
    r=request("A small original request"); original=canonical(r.to_dict())
    a=build_gemini_request(r); b=build_gemini_request(r,max_input_bytes=32768)
    assert a==b and original==canonical(r.to_dict())
    # Fixed from HEAD before the Host-limit change, using this exact fixture.
    assert hashlib.sha256(canonical(a).encode()).hexdigest()=="da60519f4ce6efad0f2ae4ef53be1d73bcd240a335ded21944b6e0f21a0b43b2"
    assert hashlib.sha256(original.encode()).hexdigest()=="a471fb75ea483ea39ce22a5b911d3044e15b356be084b8ce83c778abae7ef3e6"
    with pytest.raises(_AdapterFailure):
        build_gemini_request(request("x"*40000))
    assert build_gemini_request(request("x"*40000),max_input_bytes=196608)["model"]=="gemini-3.8-flash"


@pytest.mark.parametrize("limit", [True,0,32767,262145,3.5])
def test_invalid_adapter_host_limits_never_reach_transport(limit):
    class Transport:
        def generate_content(self,**kwargs):
            raise AssertionError("Transport must not run")
    with pytest.raises(ValidationError):
        GeminiProviderAdapter(Transport(),max_input_bytes=limit)


def test_prompts_are_generic_and_have_no_development_set_content():
    for forbidden in ('ES2002','ES2003','remote control','Play-doh','Project Manager',
                      'Industrial Designer','Marketing Executive','User Interface Designer'):
        assert forbidden not in PROMPT
    assert 'scope' in SCHEMA['properties']['tasks']['items']['properties']
    assert 'end_state' in SCHEMA['properties']['tasks']['items']['properties']


def test_actual_v2_schema_crosses_product_adapter_with_fake_transport_and_large_input():
    class Transport:
        calls = []
        def generate_content(self, *, model, contents, config):
            self.calls.append((model, contents, config))
            assert config['thinking_config']=={'thinking_level':'LOW'}
            assert config['max_output_tokens']==32768 and 'temperature' not in config
            assert config['automatic_function_calling']=={'disable':True}
            declaration=config['tools'][0]['function_declarations'][0]
            return {'candidates':[{'finish_reason':'STOP','content':{'parts':[{'function_call':{
                'name':declaration['name'],'args':{'coverage_complete':True,'tasks':[task()]}}}]}}],
                'usage_metadata':{'prompt_token_count':100,'candidates_token_count':100,'total_token_count':200}}
    transport=Transport()
    adapter=GeminiProviderAdapter(transport,max_input_bytes=196608)
    artifact=source([('X','I will send the package.'),('Y','Please do it before the next workshop.'),('X','Context. '*6000)])
    result=extractor(adapter).extract(artifact,ROSTER)
    assert len(transport.calls)==1 and len(result.candidates)==1
    assert len(canonical(transport.calls[0][1]).encode())>32768
    assert result.candidates[0].verify(artifact)

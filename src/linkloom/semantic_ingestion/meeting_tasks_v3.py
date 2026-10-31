"""Meeting v3: atomic owner/deliverable proposals and reviewable uncertainty.

The frozen v2 module is reused for exact evidence and deadline validation only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace

from linkloom.semantic_ingestion import meeting_tasks_v2 as v2
from linkloom.agents.model_adapter import ModelGenerationOptions, ModelResponse, ModelTurnRequest
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.timestamped_text import ParseStatus, parse_timestamped_text
from linkloom.tools.contracts import ToolDefinition

VERSION = "meeting-tasks/v3"
TOOL_ID = "return_meeting_tasks_v3"
canonical, digest = v2.canonical, v2.digest
# Retain all v2 source, time and scope requirements, changing only candidate
# granularity and uncertain commitments. These instructions are corpus-neutral.
PROMPT = v2.PROMPT + """
V3 CANDIDATE GRANULARITY AND CONFIDENCE:
Each candidate describes ONE deliverable for ONE owner group. Split different
owners' obligations into separate candidates even when the work is related.
Never use one person's acceptance to bind a different person's obligation.
A genuine jointly assigned deliverable may have several co-owners; independent
work assigned to different people is not joint ownership. Split independent
deliverables even within the same message. Include all relevant source witnesses.
Use different deliverable_key values for different deliverables. Group only
repeated mentions with the SAME resolved owner group AND SAME deliverable.

Do not omit casual, hesitant or underspecified undertakings. Inventory a plausible
commitment or assignment even when its object, acceptance or follow-up status is
unclear: preserve the wording, label confidence=LOW, and explain the uncertainty
in one sentence in confidence_reason. Do not invent the missing deliverable or
owner; owner_ids=[] is allowed. HIGH means an explicit, clear, adopted obligation
with a clear deliverable and supported owner group. Use LOW for tentative or
ambiguous commitments, not silence. Confidence is a model proposal, not approval.
Keep actual adopted/scope/end_state values honest: uncertain acceptance may use
adopted=false; unclear scope/state may use UNCERTAIN. Plausible LOW commitments
not explicitly completed, cancelled or confined to the meeting go to human review,
not automatic confirmation. Pure discussion without a plausible undertaking is
still not a commitment. Every HIGH and LOW candidate requires the same exact work
witnesses. Confidence never relaxes source validation. Retain completed/cancelled/
in-meeting items with their true state, as before, so the Host can exclude them.
"""
SCHEMA = deepcopy(v2.SCHEMA)
_properties = SCHEMA["properties"]["tasks"]["items"]["properties"]
_properties["confidence"] = {"type": "string", "enum": ["HIGH", "LOW"]}
_properties["confidence_reason"] = {"type": "string", "minLength": 1, "maxLength": 2048}
SCHEMA["properties"]["tasks"]["items"]["required"] = list(_properties)


@dataclass(frozen=True)
class TaskExtractionConfig(v2.TaskExtractionConfig):
    def __post_init__(self):
        if self.task_extraction_mode not in {"legacy", "meeting_v2", "meeting_v3"}:
            raise ValueError("Unknown task_extraction_mode")
        v2.TaskExtractionConfig(**{**asdict(self), "task_extraction_mode": "legacy"})


@dataclass(frozen=True)
class MeetingTaskCandidate(v2.MeetingTaskCandidate):
    confidence: str = "LOW"
    confidence_reason: str = "Human review required."

    @property
    def is_pending(self):
        if self.confidence == "HIGH":
            return super().is_pending
        return self.scope != "IN_MEETING" and self.end_state not in {"COMPLETED", "CANCELLED"}

    @property
    def review_required(self):
        return True


@dataclass(frozen=True)
class MeetingTaskBatch(v2.MeetingTaskBatch):
    def to_dict(self):
        return {"version": VERSION, **asdict(self)}


def validate_candidate(row, artifact, segments, roster, allowed_ordinals):
    if not isinstance(row, dict) or set(row) != set(_properties):
        raise ValueError("MALFORMED_TASK")
    if row["confidence"] not in {"HIGH", "LOW"}:
        raise ValueError("INVALID_CONFIDENCE")
    if not isinstance(row["confidence_reason"], str) or not row["confidence_reason"].strip() or len(row["confidence_reason"]) > 2048:
        raise ValueError("INVALID_CONFIDENCE_REASON")
    core = v2.validate_candidate({k: v for k, v in row.items() if k not in {"confidence", "confidence_reason"}},
                                 artifact, segments, roster, allowed_ordinals)
    identity = "meeting_task_v3_" + digest([VERSION, core.candidate_id, row["confidence"], row["confidence_reason"]])[:32]
    return MeetingTaskCandidate(**{f.name: getattr(core, f.name) for f in fields(core)} | {
        "candidate_id": identity, "member_ids": (identity,), "confidence": row["confidence"],
        "confidence_reason": row["confidence_reason"]})


def merge_candidates(candidates):
    # v2 already groups by exact resolved owner set AND deliverable label.
    # Retain LOW when any member is uncertain; merging cannot promote confidence.
    merged = v2.merge_candidates(candidates)
    originals = {c.candidate_id: c for c in candidates}
    result = []
    for candidate in merged:
        low = [originals[m] for m in candidate.member_ids if originals[m].confidence == "LOW"]
        result.append(replace(candidate, confidence="LOW", confidence_reason=low[0].confidence_reason) if low else candidate)
    return tuple(result)


class MeetingTaskExtractor(v2.MeetingTaskExtractor):
    def __init__(self, adapter, *, model_id, config, request_guard, on_request=None, current_source=None):
        if not isinstance(config, TaskExtractionConfig) or config.task_extraction_mode != "meeting_v3":
            raise ValueError("MeetingTaskExtractor requires explicit meeting_v3 mode")
        if not callable(getattr(adapter, "complete", None)) or not callable(request_guard):
            raise TypeError("Adapter and explicit Host request guard required")
        self.adapter, self.model_id, self.config = adapter, model_id, config
        self.guard, self.on_request, self.current_source = request_guard, on_request, current_source

    def extract(self, artifact, speakers):
        parsed = parse_timestamped_text(artifact)
        if parsed.status != ParseStatus.COMPLETE:
            raise ValueError("Complete canonical speaker parsing required")
        roster = {}
        for speaker in speakers:
            if (not isinstance(speaker, dict) or set(speaker) != {"speaker", "participant_id", "role"}
                or any(not isinstance(v, str) or not v.strip() for v in speaker.values())
                or speaker["speaker"] in roster):
                raise ValueError("Invalid trusted speaker roster")
            roster[speaker["speaker"]] = speaker
        if any(s.speaker not in roster for s in parsed.segments):
            raise ValueError("Source speaker absent from trusted roster")
        segments = parsed.segments
        def prompt(left, right):
            return PROMPT + "\nDATA:\n" + canonical({"source_version": artifact.artifact_version_id,
                "content_hash": artifact.content_hash, "speaker_roster": list(roster.values()),
                "whole_meeting": left == 0 and right == len(segments),
                "messages": [{"ordinal": s.ordinal, "speaker": s.speaker, "text": s.body_text} for s in segments[left:right]]})
        def fits(left, right, whole=False):
            return ((whole or sum(len(s.body_text.encode("utf-8")) for s in segments[left:right]) <= self.config.window_body_bytes)
                and len(canonical(prompt(left, right)).encode("utf-8")) <= self.config.max_input_bytes)
        windows = []
        if fits(0, len(segments), True):
            windows = [(0, len(segments))]
        else:
            left = 0
            while left < len(segments):
                low, high = left + 1, len(segments)
                if not fits(left, low):
                    raise ValueError("One complete utterance exceeds Host window limits; source not truncated")
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(left, middle):
                        low = middle
                    else:
                        high = middle - 1
                windows.append((left, low))
                if low == len(segments):
                    break
                left = max(left + 1, low - self.config.overlap_messages)
        if len(windows) > self.config.max_calls:
            raise ValueError("Meeting windows exceed Host call limit; no requests made")
        candidates, rejected, responses, coverage = [], [], [], True
        for index, (left, right) in enumerate(windows):
            text = prompt(left, right)
            fingerprint = digest([VERSION, text, asdict(self.config), self.model_id])[:32]
            request = ModelTurnRequest(run_id="meeting_" + fingerprint, turn_id="turn_" + fingerprint,
                task_id="task_" + fingerprint, agent_id="meeting-task-extractor-v2", sequence=1,
                user_input=text, observation=None,
                available_tools=[ToolDefinition(tool_id=TOOL_ID, version=VERSION, description="Return meeting task proposals only.",
                    input_schema=SCHEMA, output_schema=SCHEMA)], model_id=self.model_id,
                generation_options=ModelGenerationOptions(max_output_tokens=self.config.max_output_tokens,
                    temperature=None, require_tool_call=True, thinking_level="low"))
            self._current(artifact)
            if self.guard(request) is not True:
                raise PermissionError("Host denied meeting task request")
            if self.on_request:
                self.on_request(index, request)
            response = self.adapter.complete(request)
            self._current(artifact)
            if not isinstance(response, ModelResponse) or response.error:
                raise RuntimeError("Meeting task provider failed; no automatic retry")
            if response.proposal is not None:
                calls = response.proposal.tool_calls
                call = calls[0].runtime_call if response.proposal.kind == "tool_calls" and len(calls) == 1 else None
            else:
                call = response.action.tool_call if response.action and response.action.kind == "tool_call" else None
            if call is None or call.tool_id != TOOL_ID:
                raise ValueError("Exactly one meeting task tool call required")
            payload = call.arguments
            if (not isinstance(payload, dict) or set(payload) != {"coverage_complete", "tasks"}
                or type(payload["coverage_complete"]) is not bool or not isinstance(payload["tasks"], list)
                or len(payload["tasks"]) > 80):
                raise ValueError("Malformed meeting task response")
            responses.append(payload)
            coverage &= payload["coverage_complete"] and len(payload["tasks"]) < 80
            for item, row in enumerate(payload["tasks"]):
                try:
                    candidates.append(validate_candidate(row, artifact, segments, roster, {s.ordinal for s in segments[left:right]}))
                except (ValueError, TypeError, KeyError) as error:
                    rejected.append({"window": index, "candidate_index": item, "reason": str(error)})
                    coverage = False
        merged = merge_candidates(candidates)
        self._current(artifact)
        return MeetingTaskBatch(tuple(c for c in merged if c.is_pending), tuple(c for c in merged if not c.is_pending),
            tuple(rejected), tuple({"first_ordinal": segments[l].ordinal, "last_ordinal": segments[r-1].ordinal,
                "whole_meeting": len(windows) == 1} for l, r in windows), bool(coverage), tuple(responses))

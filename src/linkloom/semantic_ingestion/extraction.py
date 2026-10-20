"""Provider-neutral structured semantic extraction over validated source segments."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Protocol

from linkloom.agents.model_adapter import (
    ModelGenerationOptions,
    ModelProviderAdapter,
    ModelResponse,
    ModelUsage,
    ModelTurnRequest,
)
from linkloom.semantic_ingestion.candidate_models import (
    CandidateReasonCode,
    CandidateConfidence,
    ClaimType,
    ExtractionAttempt,
    ExtractionReceipt,
    ExtractedClaim,
    SemanticExtractionResult,
    TemporalResolution,
    claims_sha256_for,
    extraction_fingerprint_for,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import TimestampedTextSegment
from linkloom.tools.contracts import ToolCall, ToolDefinition


SEMANTIC_EXTRACTION_SCHEMA_VERSION = "semantic-candidate/v1"
SEMANTIC_EXTRACTOR_VERSION = "provider-neutral-extractor/v1"
TEMPORAL_POLICY_VERSION = "explicit-time-only/v1"
SEMANTIC_EXTRACTION_TOOL_ID = "emit_semantic_candidates"
_DEFAULT_TEMPERATURE = object()
_MAX_CLAIMS_PER_SEGMENT = 20
_MAX_FIELD_LENGTH = 512
_PROVENANCE_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_version_id",
        "char_end",
        "char_start",
        "content_hash",
        "evidence_ref",
        "line_end",
        "line_start",
        "message_id",
        "quote",
        "quote_sha256",
        "segment_id",
        "source_episode_id",
        "source_ref",
        "ingestion_time",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nullable_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_FIELD_LENGTH:
        raise ValueError(f"{field_name} must be a non-empty bounded string or null")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value.strip()


def _nullable_date_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        raise ValueError(f"{field_name} must be a non-empty date string or null")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value.strip()


def _extraction_schema() -> dict[str, Any]:
    claim_type_values = [item.value for item in ClaimType]
    temporal_values = [item.value for item in TemporalResolution]
    nullable_string = {"type": ["string", "null"], "maxLength": _MAX_FIELD_LENGTH}
    confidence_properties = {
        name: {"type": "number", "minimum": 0, "maximum": 1}
        for name in ("claim_type", "entity", "relation", "temporal", "overall")
    }
    claim_properties = {
        "claim_type": {"type": "string", "enum": claim_type_values},
        "subject": nullable_string,
        "relation": nullable_string,
        "value": nullable_string,
        "temporal_status": {"type": "string", "enum": temporal_values},
        "valid_from": {"type": ["string", "null"], "maxLength": 40},
        "valid_to": {"type": ["string", "null"], "maxLength": 40},
        "confidence": {
            "type": "object",
            "properties": confidence_properties,
            "required": list(confidence_properties),
            "additionalProperties": False,
        },
    }
    claim_schema = {
        "type": "object",
        "properties": claim_properties,
        "required": list(claim_properties),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": claim_schema,
                "maxItems": _MAX_CLAIMS_PER_SEGMENT,
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


_EXTRACTION_SCHEMA = _extraction_schema()
_EXTRACTION_SCHEMA_SHA256 = _sha256(_canonical_json(_EXTRACTION_SCHEMA))


@dataclass(frozen=True, slots=True)
class ProviderRequestAuthorization:
    """A budget guard's auditable approval for one exact normalized request."""

    guard_id: str
    request_sha256: str
    reservation_id: str
    estimated_cost_upper_bound_usd: float
    authorized: bool = True

    def __post_init__(self) -> None:
        for name in ("guard_id", "reservation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.request_sha256):
            raise ValueError("request_sha256 must be a lowercase SHA-256 digest")
        if (
            isinstance(self.estimated_cost_upper_bound_usd, bool)
            or not isinstance(self.estimated_cost_upper_bound_usd, (int, float))
            or not math.isfinite(self.estimated_cost_upper_bound_usd)
            or self.estimated_cost_upper_bound_usd < 0
        ):
            raise ValueError("estimated cost upper bound must be finite and non-negative")
        if not isinstance(self.authorized, bool):
            raise ValueError("authorized must be boolean")

    @classmethod
    def approve(
        cls,
        request: ModelTurnRequest,
        *,
        guard_id: str,
        reservation_id: str,
        estimated_cost_upper_bound_usd: float,
    ) -> "ProviderRequestAuthorization":
        request_sha256 = _sha256(_canonical_json(request.to_dict()))
        return cls(
            guard_id=guard_id,
            request_sha256=request_sha256,
            reservation_id=reservation_id,
            estimated_cost_upper_bound_usd=estimated_cost_upper_bound_usd,
        )


@dataclass(frozen=True, slots=True)
class SemanticExtractionContext:
    expected_workspace_id: str
    relation_resolver: FrozenRelationResolver

    def __post_init__(self) -> None:
        if not isinstance(self.expected_workspace_id, str) or not self.expected_workspace_id.strip():
            raise ValueError("expected_workspace_id is required")
        if not isinstance(self.relation_resolver, FrozenRelationResolver):
            raise ValueError("relation_resolver must be FrozenRelationResolver")


class SemanticExtractor(Protocol):
    """Provider-independent semantic extraction contract."""

    def extract(
        self,
        segment: TimestampedTextSegment,
        context: SemanticExtractionContext,
    ) -> SemanticExtractionResult:
        ...


def _prompt(segment: TimestampedTextSegment, context: SemanticExtractionContext, version: str) -> str:
    instructions = {
        "prompt_version": version,
        "schema_version": SEMANTIC_EXTRACTION_SCHEMA_VERSION,
        "allowed_claim_types": [item.value for item in ClaimType],
        "temporal_statuses": [item.value for item in TemporalResolution],
        "canonical_relations": list(context.relation_resolver.canonical_relations),
        "relation_aliases": dict(context.relation_resolver.aliases),
        "source_segment": {
            "speaker": segment.speaker,
            "event_time": segment.event_time.isoformat(),
            "text": segment.body_text,
        },
    }
    return (
        "Extract only source-supported semantic claims from the supplied segment. "
        "Return one structured tool call matching the schema. Preserve the distinction "
        "between DECISION, FACT, PROPOSAL, QUESTION, REJECTED_OPTION, PLAN, ACTION, "
        "OPINION, and SPECULATION. A DECISION is an explicit team choice, approval, "
        "adoption, switch, replacement, cancellation, or commitment that determines "
        "what the team will do. A FACT is an observation or state that does not itself "
        "represent a team choice or commitment. When the source explicitly states that "
        "the team replaces, supersedes, switches, adopts, approves, or changes a prior "
        "choice to another option, classify that expressed choice or change as DECISION, "
        "including when it is phrased as a replacement rather than with the words "
        "\"we decided\". Do not infer a decision solely from a changed state or from "
        "historical reporting alone: reporting that another person or team changed "
        "something, without the current source speaker or team selecting or committing "
        "to it, remains FACT. A current state without decision language remains FACT. "
        "Statements merely considering or proposing an option remain PROPOSAL. A "
        "question, rejected option, plan, action, opinion, or speculation is not a "
        "decision. Do not invent a subject, value, "
        "relation, effective date, entity, workspace, artifact identity, or evidence "
        "reference. Output null for unknown fields. SOURCE_EVENT_TIME means the source "
        "event time is explicitly the effective time. For SOURCE_EVENT_TIME, return null "
        "for valid_from and valid_to; the local temporal policy derives the source event "
        "time. EXPLICIT means valid_from/valid_to "
        "is copied from an explicit source date or timestamp; use UNRESOLVED for relative "
        "or ambiguous time and NOT_STATED when no effective-time claim is made. Relation "
        "phrases may be canonical, an explicit alias, or a new relation proposal. "
        "Confidence values are informational and never override local validation. Input:\n"
        + json.dumps(instructions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _request_ids(fingerprint: str, attempt: int) -> tuple[str, str, str, str]:
    run_id = f"semrun_{fingerprint[:24]}"
    turn_id = f"semturn_{fingerprint[:20]}_{attempt}"
    task_id = f"semtask_{fingerprint[:20]}"
    agent_id = "semantic-extractor-v1"
    return run_id, turn_id, task_id, agent_id


def _provider_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id=SEMANTIC_EXTRACTION_TOOL_ID,
        version=SEMANTIC_EXTRACTION_SCHEMA_VERSION,
        description="Return structured source-grounded claim proposals; never write memory.",
        input_schema=deepcopy(_EXTRACTION_SCHEMA),
        output_schema=deepcopy(_EXTRACTION_SCHEMA),
    )


def _extract_tool_arguments(response: ModelResponse) -> dict[str, Any]:
    call: ToolCall | None = None
    if response.proposal is not None:
        proposal = response.proposal
        if proposal.kind != "tool_calls" or len(proposal.tool_calls) != 1:
            raise ValueError("exactly one structured extraction tool call is required")
        call = proposal.tool_calls[0].runtime_call
    elif response.action is not None and response.action.kind == "tool_call":
        call = response.action.tool_call
    if call is None or call.tool_id != SEMANTIC_EXTRACTION_TOOL_ID:
        raise ValueError("provider response did not return the extraction tool call")
    return call.arguments


def _unsupported_output_reason(
    data: Any,
    expected_workspace_id: str,
) -> CandidateReasonCode | None:
    if not isinstance(data, dict):
        return CandidateReasonCode.MALFORMED_EXTRACTION
    if "workspace_id" in data:
        if data["workspace_id"] != expected_workspace_id:
            return CandidateReasonCode.WORKSPACE_MISMATCH
        return CandidateReasonCode.MALFORMED_EXTRACTION
    if _PROVENANCE_FIELDS.intersection(data):
        return CandidateReasonCode.PROVENANCE_INVALID
    raw_claims = data.get("claims")
    if not isinstance(raw_claims, list):
        return None
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            continue
        if "workspace_id" in raw_claim:
            if raw_claim["workspace_id"] != expected_workspace_id:
                return CandidateReasonCode.WORKSPACE_MISMATCH
            return CandidateReasonCode.MALFORMED_EXTRACTION
        if _PROVENANCE_FIELDS.intersection(raw_claim):
            return CandidateReasonCode.PROVENANCE_INVALID
    return None


def _parse_claim(data: Any) -> ExtractedClaim:
    if not isinstance(data, dict):
        raise ValueError("each claim must be an object")
    expected_keys = {
        "claim_type",
        "subject",
        "relation",
        "value",
        "temporal_status",
        "valid_from",
        "valid_to",
        "confidence",
    }
    if set(data) != expected_keys:
        raise ValueError("claim has missing or unsupported fields")
    try:
        claim_type = ClaimType(data["claim_type"])
    except (TypeError, ValueError) as error:
        raise _SemanticPayloadError(CandidateReasonCode.SEMANTIC_CLASS_UNRESOLVED) from error
    try:
        temporal_status = TemporalResolution(data["temporal_status"])
    except (TypeError, ValueError) as error:
        raise ValueError("temporal_status is not in the schema") from error

    raw_confidence = data["confidence"]
    if not isinstance(raw_confidence, dict) or set(raw_confidence) != {
        "claim_type",
        "entity",
        "relation",
        "temporal",
        "overall",
    }:
        raise ValueError("confidence must contain exactly the five declared scores")
    confidence = CandidateConfidence(
        claim_type=raw_confidence["claim_type"],
        entity=raw_confidence["entity"],
        relation=raw_confidence["relation"],
        temporal=raw_confidence["temporal"],
        overall=raw_confidence["overall"],
    )
    subject = _nullable_text(data["subject"], "subject")
    relation = _nullable_text(data["relation"], "relation")
    value = _nullable_text(data["value"], "value")
    valid_from = _nullable_date_text(data["valid_from"], "valid_from")
    valid_to = _nullable_date_text(data["valid_to"], "valid_to")
    return ExtractedClaim(
        claim_type=claim_type,
        subject=subject,
        relation_phrase=relation,
        value=value,
        temporal_status=temporal_status,
        valid_from_raw=valid_from,
        valid_to_raw=valid_to,
        confidence=confidence,
    )


class _SemanticPayloadError(ValueError):
    def __init__(self, reason: CandidateReasonCode) -> None:
        super().__init__(reason.value)
        self.reason = reason


class ProviderNeutralSemanticExtractor:
    """Use the existing ModelProviderAdapter contract for one structured turn.

    Only retry explicit retryable provider failures with a known failed outcome.
    A received response, malformed output, unknown outcome, or local exception is
    never replayed by this extractor.
    """

    def __init__(
        self,
        provider: ModelProviderAdapter,
        *,
        provider_id: str,
        model_id: str,
        request_guard: Callable[[ModelTurnRequest], ProviderRequestAuthorization],
        request_guard_id: str,
        max_attempts: int = 1,
        prompt_version: str = "semantic-extraction/v1",
        max_output_tokens: int = 1536,
        temperature: float | None | object = _DEFAULT_TEMPERATURE,
        thinking_level: str | None = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise ValueError("provider must implement ModelProviderAdapter.complete")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id is required")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        if not callable(request_guard):
            raise ValueError("request_guard is required to authorize each Provider call")
        if not isinstance(request_guard_id, str) or not request_guard_id.strip():
            raise ValueError("request_guard_id is required")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if not isinstance(prompt_version, str) or not prompt_version.strip():
            raise ValueError("prompt_version is required")
        self._provider = provider
        self.provider_id = provider_id.strip()
        self.model_id = model_id.strip()
        self._request_guard = request_guard
        self.request_guard_id = request_guard_id.strip()
        self.max_attempts = max_attempts
        self.prompt_version = prompt_version.strip()
        is_gemini_3 = (
            provider_id.strip().casefold() == "gemini"
            and model_id.strip().casefold().startswith("gemini-3")
        )
        if temperature is _DEFAULT_TEMPERATURE:
            temperature = None if is_gemini_3 else 0
        if thinking_level is None and is_gemini_3:
            thinking_level = "low"
        self.generation_options = ModelGenerationOptions(
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            require_tool_call=True,
            thinking_level=thinking_level,
        )

    def extract(
        self,
        segment: TimestampedTextSegment,
        context: SemanticExtractionContext,
    ) -> SemanticExtractionResult:
        if not isinstance(segment, TimestampedTextSegment):
            raise TypeError("segment must be a Step 1 TimestampedTextSegment")
        if not isinstance(context, SemanticExtractionContext):
            raise TypeError("context must be SemanticExtractionContext")

        prompt = _prompt(segment, context, self.prompt_version)
        prompt_sha256 = _sha256(prompt.encode("utf-8"))
        generation_options = self.generation_options
        config_sha256 = _sha256(
            _canonical_json(
                {
                    "provider_id": self.provider_id,
                    "model_id": self.model_id,
                    "extractor_version": SEMANTIC_EXTRACTOR_VERSION,
                    "schema_version": SEMANTIC_EXTRACTION_SCHEMA_VERSION,
                    "temporal_policy_version": TEMPORAL_POLICY_VERSION,
                    "schema_sha256": _EXTRACTION_SCHEMA_SHA256,
                    "prompt_version": self.prompt_version,
                    "generation_options": generation_options.to_dict(),
                    "max_attempts": self.max_attempts,
                    "request_guard_id": self.request_guard_id,
                    "relation_catalog_fingerprint": context.relation_resolver.fingerprint,
                }
            )
        )
        extraction_fingerprint = extraction_fingerprint_for(
            workspace_id=segment.evidence_span.workspace_id,
            artifact_version_id=segment.evidence_span.artifact_version_id,
            segment_id=segment.segment_id,
            evidence_ref=segment.evidence_span.evidence_ref,
            quote_sha256=segment.evidence_span.quote_sha256,
            config_sha256=config_sha256,
            prompt_sha256=prompt_sha256,
        )
        attempts: list[ExtractionAttempt] = []
        terminal_reason: CandidateReasonCode | None = None
        parsed_claims: tuple[ExtractedClaim, ...] = ()
        response_evidence: dict[str, object] | None = None

        for attempt_number in range(1, self.max_attempts + 1):
            run_id, turn_id, task_id, agent_id = _request_ids(
                extraction_fingerprint,
                attempt_number,
            )
            request = ModelTurnRequest(
                run_id=run_id,
                turn_id=turn_id,
                task_id=task_id,
                agent_id=agent_id,
                sequence=attempt_number,
                user_input=prompt,
                observation=None,
                available_tools=[_provider_tool()],
                model_id=self.model_id,
                generation_options=generation_options,
            )
            request_sha256 = _sha256(_canonical_json(request.to_dict()))
            started = time.perf_counter()
            authorization: ProviderRequestAuthorization | None = None
            try:
                guard_result = self._request_guard(deepcopy(request))
                if not isinstance(guard_result, ProviderRequestAuthorization):
                    raise PermissionError("provider request guard denied request")
                authorization = guard_result
                if (
                    not authorization.authorized
                    or authorization.guard_id != self.request_guard_id
                    or authorization.request_sha256 != request_sha256
                ):
                    raise PermissionError("provider request guard denied or mismatched request")
            except Exception:
                attempts.append(
                    ExtractionAttempt(
                        attempt_number=attempt_number,
                        request_sha256=request_sha256,
                        response_sha256=None,
                        provider_request_id=None,
                        provider_response_id=None,
                        usage=ModelUsage(),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        failure_code=CandidateReasonCode.PROVIDER_REQUEST_BLOCKED.value,
                        outcome="preflight_blocked",
                        budget_reservation_id=(
                            authorization.reservation_id if authorization is not None else None
                        ),
                        estimated_cost_upper_bound_usd=(
                            authorization.estimated_cost_upper_bound_usd
                            if authorization is not None
                            else None
                        ),
                    )
                )
                terminal_reason = CandidateReasonCode.PROVIDER_REQUEST_BLOCKED
                break
            try:
                response = self._provider.complete(request)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                attempts.append(
                    ExtractionAttempt(
                        attempt_number=attempt_number,
                        request_sha256=request_sha256,
                        response_sha256=None,
                        provider_request_id=None,
                        provider_response_id=None,
                        usage=ModelUsage(),
                        latency_ms=elapsed_ms,
                        failure_code="PROVIDER_CALL_EXCEPTION",
                        outcome="provider_call_exception",
                        budget_reservation_id=authorization.reservation_id,
                        estimated_cost_upper_bound_usd=authorization.estimated_cost_upper_bound_usd,
                    )
                )
                terminal_reason = CandidateReasonCode.EXTRACTION_FAILED
                break

            elapsed_ms = (time.perf_counter() - started) * 1000
            if not isinstance(response, ModelResponse):
                attempts.append(
                    ExtractionAttempt(
                        attempt_number=attempt_number,
                        request_sha256=request_sha256,
                        response_sha256=None,
                        provider_request_id=None,
                        provider_response_id=None,
                        usage=ModelUsage(),
                        latency_ms=elapsed_ms,
                        failure_code="PROVIDER_RESPONSE_TYPE_INVALID",
                        outcome="provider_call_exception",
                        budget_reservation_id=authorization.reservation_id,
                        estimated_cost_upper_bound_usd=authorization.estimated_cost_upper_bound_usd,
                    )
                )
                terminal_reason = CandidateReasonCode.EXTRACTION_FAILED
                break

            response_sha256 = _sha256(_canonical_json(response.to_dict()))
            # The response contract already rejects persisted secret keys. Omit
            # provider metadata and continuations, which are not parser inputs.
            response_evidence = response.to_dict()
            metadata = response_evidence.get("provider_metadata")
            response_evidence["provider_metadata"] = (
                {"http_status": metadata["http_status"]}
                if isinstance(metadata, dict) and isinstance(metadata.get("http_status"), int)
                else {}
            )
            response_evidence.pop("provider_continuation", None)
            response_evidence.pop("provider_turn_continuation", None)
            if response.error is not None:
                error = response.error
                known_failure = error.outcome == "known_failure"
                attempts.append(
                    ExtractionAttempt(
                        attempt_number=attempt_number,
                        request_sha256=request_sha256,
                        response_sha256=response_sha256,
                        provider_request_id=response.provider_request_id or error.provider_request_id,
                        provider_response_id=response.provider_response_id,
                        usage=response.usage,
                        latency_ms=elapsed_ms,
                        failure_code=error.code,
                        outcome=("known_provider_failure" if known_failure else "unknown_provider_outcome"),
                        budget_reservation_id=authorization.reservation_id,
                        estimated_cost_upper_bound_usd=authorization.estimated_cost_upper_bound_usd,
                    )
                )
                terminal_reason = CandidateReasonCode.EXTRACTION_FAILED
                if known_failure and error.retryable and attempt_number < self.max_attempts:
                    terminal_reason = None
                    continue
                break

            try:
                payload = _extract_tool_arguments(response)
                unsupported_reason = _unsupported_output_reason(
                    payload,
                    context.expected_workspace_id,
                )
                if unsupported_reason is not None:
                    raise _SemanticPayloadError(unsupported_reason)
                if set(payload) != {"claims"}:
                    raise ValueError("extraction result has missing or unsupported fields")
                raw_claims = payload["claims"]
                if not isinstance(raw_claims, list) or len(raw_claims) > _MAX_CLAIMS_PER_SEGMENT:
                    raise ValueError("claims must be a bounded array")
                parsed_claims = tuple(_parse_claim(item) for item in raw_claims)
            except _SemanticPayloadError as error:
                terminal_reason = error.reason
            except (TypeError, ValueError, KeyError):
                terminal_reason = CandidateReasonCode.MALFORMED_EXTRACTION

            attempts.append(
                ExtractionAttempt(
                    attempt_number=attempt_number,
                    request_sha256=request_sha256,
                    response_sha256=response_sha256,
                    provider_request_id=response.provider_request_id,
                    provider_response_id=response.provider_response_id,
                    usage=response.usage,
                    latency_ms=elapsed_ms,
                    failure_code=terminal_reason.value if terminal_reason is not None else None,
                    outcome="accepted_response",
                    budget_reservation_id=authorization.reservation_id,
                    estimated_cost_upper_bound_usd=authorization.estimated_cost_upper_bound_usd,
                )
            )
            # Never replay an accepted response after local parsing or validation fails.
            break

        receipt = ExtractionReceipt(
            provider_id=self.provider_id,
            model_id=self.model_id,
            extractor_version=SEMANTIC_EXTRACTOR_VERSION,
            schema_version=SEMANTIC_EXTRACTION_SCHEMA_VERSION,
            temporal_policy_version=TEMPORAL_POLICY_VERSION,
            request_guard_id=self.request_guard_id,
            workspace_id=segment.evidence_span.workspace_id,
            artifact_version_id=segment.evidence_span.artifact_version_id,
            segment_id=segment.segment_id,
            evidence_ref=segment.evidence_span.evidence_ref,
            quote_sha256=segment.evidence_span.quote_sha256,
            relation_catalog_fingerprint=context.relation_resolver.fingerprint,
            prompt_sha256=prompt_sha256,
            schema_sha256=_EXTRACTION_SCHEMA_SHA256,
            config_sha256=config_sha256,
            extraction_fingerprint=extraction_fingerprint,
            claims_sha256=claims_sha256_for(
                parsed_claims if terminal_reason is None else ()
            ),
            attempts=tuple(attempts),
            terminal_reason=terminal_reason,
        )
        return SemanticExtractionResult(
            claims=parsed_claims if terminal_reason is None else (),
            receipt=receipt,
            failure_code=terminal_reason,
            response_evidence=response_evidence,
        )

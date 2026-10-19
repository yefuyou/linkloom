"""Load and verify the byte-frozen synthetic M3-L Canary package.

This module is deliberately a local, read-only boundary.  It loads the
checked-in synthetic source texts and evaluation/configuration artifacts; it
does not construct a Provider, read a vault, or make a network request.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence


CANARY_CAMPAIGN_ID = "m3-product-canary-v1"
CANARY_MODEL_ID = "gemini-3.8-flash"
CANARY_SPEC_VERSION = "canary-v2"
HISTORICAL_CANARY_SPEC_VERSION = "canary-v1"

# This digest is intentionally pinned in source.  A changed manifest is a new
# freeze and must not be silently accepted by an existing Canary loader.
EXPECTED_FREEZE_MANIFEST_SHA256 = "72d8d7b3cce4180d57bde436614d2050f0822f0c4ffe3dc6291173f86f898cf8"
EXPECTED_V1_FREEZE_MANIFEST_SHA256 = "348726410439026f54906b4268a2d4b108a48f3088ed5c3e15658f3d94c824f4"

_PACKAGE_DIR = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "requirements"
    / "m3-l-live-canary"
    / "canary-v2"
)
_EXPECTED_ARTIFACTS = frozenset(
    {"README.md", "sources.json", "cases.json", "rubric.json", "runtime_config.json"}
)
_EXPECTED_SOURCE_IDS = ("A", "B", "C", "D")
_EXPECTED_CASE_IDS = ("C1", "C2", "C3")
_EXPECTED_CATEGORIES = (
    "CORRECT_PARTIAL",
    "WRONG_ANSWER",
    "CONTRACT_FAILURE",
    "INFRA_FAILURE",
)
_EXPECTED_DIMENSIONS = (
    "continuity_coverage",
    "precise_quote_dates",
    "conflict",
    "unconfirmed_assertions",
    "excess_planning_missing_investigation",
)


class CanarySpecError(ValueError):
    """Raised when the frozen package is missing, malformed, or tampered with."""


def _freeze(value: Any) -> Any:
    """Recursively make JSON values immutable for the returned public API."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanarySpecError(f"{field_name} must be an object")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CanarySpecError(f"{field_name} must be non-empty text")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CanarySpecError(f"{field_name} must be a positive integer")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(root: Path, name: str) -> Mapping[str, Any]:
    path = root / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CanarySpecError(f"unable to read {name}: {error}") from error
    return _mapping(value, name)


@dataclass(frozen=True, slots=True)
class CanaryCase:
    """One immutable case-to-source and request-budget binding."""

    case_id: str
    source_ids: tuple[str, ...]
    stage_requests: Mapping[str, int]
    max_requests: int

    def __getitem__(self, key: str) -> Any:
        if key == "id" or key == "case_id":
            return self.case_id
        if key == "sources" or key == "source_ids":
            return self.source_ids
        if key == "stage_requests":
            return self.stage_requests
        if key == "max_requests":
            return self.max_requests
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True, slots=True)
class CanaryConfig:
    """Immutable model and budget configuration for the frozen campaign."""

    campaign_id: str
    model_id: str
    provider_id: str
    campaign_total_seconds: int
    case_total_seconds: int
    request_timeout_seconds: int
    sdk_attempts: int
    automatic_function_calling: bool
    thinking_level: str
    max_input_bytes: int
    max_requests: int
    max_cost_usd: int | float
    per_request: Mapping[str, int]
    stage_requests: Mapping[str, Mapping[str, int]]
    provider_requests_are_forbidden_in_offline_checks: bool

    def __getitem__(self, key: str) -> Any:
        if key == "campaign_id":
            return self.campaign_id
        if key == "model_id":
            return self.model_id
        if key == "provider_id":
            return self.provider_id
        if key == "campaign_total_seconds":
            return self.campaign_total_seconds
        if key == "case_total_seconds":
            return self.case_total_seconds
        if key == "request_timeout_seconds":
            return self.request_timeout_seconds
        if key == "sdk_attempts":
            return self.sdk_attempts
        if key == "automatic_function_calling":
            return self.automatic_function_calling
        if key == "thinking_level":
            return self.thinking_level
        if key == "max_input_bytes":
            return self.max_input_bytes
        if key == "max_requests":
            return self.max_requests
        if key == "max_cost_usd":
            return self.max_cost_usd
        if key == "per_request":
            return self.per_request
        if key == "stage_requests":
            return self.stage_requests
        if key == "provider_requests_are_forbidden_in_offline_checks":
            return self.provider_requests_are_forbidden_in_offline_checks
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True, slots=True)
class CanarySpec(Mapping[str, Any]):
    """Public frozen spec view used by the offline campaign factory.

    The mapping surface intentionally exposes the small product-facing shape
    (`question`, `sources`, `cases`, `config`, and `digest`) while attributes
    provide typed access for tests and review tools.
    """

    campaign_id: str
    model_id: str
    question: str
    sources: Mapping[str, str]
    cases: Mapping[str, CanaryCase]
    config: CanaryConfig
    rubric: Mapping[str, Any]
    digest: str
    source_metadata: Mapping[str, Mapping[str, str]]

    @property
    def case_config(self) -> Mapping[str, CanaryCase]:
        return self.cases

    @property
    def source_texts(self) -> Mapping[str, str]:
        return self.sources

    @property
    def inference_payload(self) -> Mapping[str, Any]:
        """Return only the model input fields; rubric data stays out of it."""
        return MappingProxyType(
            {
                "question": self.question,
                "sources": self.sources,
            }
        )

    def match_sources(
        self,
        query: str,
        sources: Mapping[str, str] | Sequence[str],
    ) -> tuple[CanaryCase, tuple[str, ...]]:
        """Resolve one frozen case from an exact question and source texts.

        Host code can pass its source catalog as either a mapping of frozen
        IDs to exact text, a sequence of frozen IDs, or a sequence of exact
        texts.  Matching is intentionally exact: a changed historical record
        must be rejected instead of being silently assigned to a case.
        """
        if query != self.question:
            raise CanarySpecError("query does not match the frozen common question")
        if isinstance(sources, Mapping):
            if not sources:
                raise CanarySpecError("at least one frozen source is required")
            source_ids = tuple(sources)
            if any(source_id not in self.sources for source_id in source_ids):
                raise CanarySpecError("source mapping contains an unknown frozen source")
            if any(sources[source_id] != self.sources[source_id] for source_id in source_ids):
                raise CanarySpecError("source mapping contains changed historical bytes")
        else:
            values = tuple(sources)
            if not values:
                raise CanarySpecError("at least one frozen source is required")
            # Host source catalogs commonly pass the raw source text in
            # historical order.  A mapping record with ``content`` is also
            # accepted so the freeze boundary does not need to know the
            # host's source-ID format.
            if all(isinstance(value, str) and value in self.sources for value in values):
                source_ids = values
            else:
                reverse = {text: source_id for source_id, text in self.sources.items()}
                resolved: list[str] = []
                for value in values:
                    content = value.get("content") if isinstance(value, Mapping) else value
                    if not isinstance(content, str) or content not in reverse:
                        raise CanarySpecError("source sequence is not a frozen ID or exact source text")
                    resolved.append(reverse[content])
                source_ids = tuple(resolved)
        if len(set(source_ids)) != len(source_ids):
            raise CanarySpecError("source selection contains duplicates")
        for case in self.cases.values():
            if set(source_ids) == set(case.source_ids):
                return case, case.source_ids
        raise CanarySpecError(f"source selection does not match a frozen case: {source_ids!r}")

    def __getitem__(self, key: str) -> Any:
        if key == "campaign_id":
            return self.campaign_id
        if key == "model_id":
            return self.model_id
        if key == "question":
            return self.question
        if key == "sources":
            return self.sources
        if key == "cases" or key == "case_config":
            return self.cases
        if key == "config":
            return self.config
        if key == "rubric":
            return self.rubric
        if key == "digest":
            return self.digest
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("campaign_id", "model_id", "question", "sources", "cases", "config", "rubric", "digest"))

    def __len__(self) -> int:
        return 8


def _validate_manifest(root: Path) -> tuple[Mapping[str, Any], str]:
    manifest_path = root / "freeze_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CanarySpecError("freeze_manifest.json is missing")
    manifest_digest = _sha256(manifest_path)
    if manifest_digest != EXPECTED_FREEZE_MANIFEST_SHA256:
        raise CanarySpecError(
            "freeze manifest SHA256 mismatch: expected pinned digest "
            f"{EXPECTED_FREEZE_MANIFEST_SHA256}, got {manifest_digest}"
        )
    manifest = _read_json(root, "freeze_manifest.json")
    if manifest.get("manifest_version") != "canary-freeze-manifest/v1":
        raise CanarySpecError("unsupported freeze manifest version")
    if manifest.get("campaign_id") != CANARY_CAMPAIGN_ID:
        raise CanarySpecError("freeze manifest campaign ID mismatch")
    if manifest.get("spec_version") != CANARY_SPEC_VERSION:
        raise CanarySpecError("freeze manifest spec version mismatch")
    if manifest.get("hash_algorithm") != "sha256":
        raise CanarySpecError("freeze manifest must use SHA256")
    files = _mapping(manifest.get("files"), "freeze manifest files")
    if set(files) != _EXPECTED_ARTIFACTS:
        raise CanarySpecError("freeze manifest artifact list mismatch")
    actual_files = {path.name for path in root.iterdir() if path.is_file() and path.name != "freeze_manifest.json"}
    if actual_files != set(files):
        raise CanarySpecError("frozen package contains an unlisted artifact")
    for relative_name, expected in files.items():
        if not isinstance(relative_name, str) or Path(relative_name).is_absolute() or ".." in Path(relative_name).parts:
            raise CanarySpecError(f"invalid frozen artifact path: {relative_name!r}")
        if not isinstance(expected, str) or len(expected) != 64:
            raise CanarySpecError(f"invalid SHA256 for {relative_name}")
        path = root / relative_name
        if path.is_symlink() or path.resolve().parent != root.resolve() or not path.is_file():
            raise CanarySpecError(f"frozen artifact is missing: {relative_name}")
        actual = _sha256(path)
        if actual != expected.lower():
            raise CanarySpecError(f"{relative_name} SHA256 mismatch: expected {expected}, got {actual}")
    return manifest, manifest_digest


def _load_cases(payload: Mapping[str, Any], source_ids: set[str]) -> Mapping[str, CanaryCase]:
    if payload.get("schema_version") != "canary-cases/v1":
        raise CanarySpecError("unsupported canary cases schema")
    if payload.get("question_id") != "common":
        raise CanarySpecError("canary cases must use the common question")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or tuple(item.get("id") for item in raw_cases if isinstance(item, Mapping)) != _EXPECTED_CASE_IDS:
        raise CanarySpecError("canary case IDs must be C1, C2, C3 in order")
    cases: dict[str, CanaryCase] = {}
    for item in raw_cases:
        record = _mapping(item, "case")
        case_id = _text(record.get("id"), "case id")
        raw_sources = record.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources or any(not isinstance(value, str) for value in raw_sources):
            raise CanarySpecError(f"{case_id} sources must be a non-empty list")
        selected_sources = tuple(raw_sources)
        if any(value not in source_ids for value in selected_sources) or len(set(selected_sources)) != len(selected_sources):
            raise CanarySpecError(f"{case_id} references invalid or duplicate sources")
        raw_stage = _mapping(record.get("stage_requests"), f"{case_id} stage_requests")
        if set(raw_stage) != {"F", "G", "Semantic", "Runtime"}:
            raise CanarySpecError(f"{case_id} stage request keys mismatch")
        stage_requests = MappingProxyType({name: _positive_int(value, f"{case_id}.{name}") for name, value in raw_stage.items()})
        max_requests = _positive_int(record.get("max_requests"), f"{case_id}.max_requests")
        if sum(stage_requests.values()) != max_requests:
            raise CanarySpecError(f"{case_id} max_requests does not equal stage request total")
        cases[case_id] = CanaryCase(case_id, selected_sources, stage_requests, max_requests)
    return MappingProxyType(cases)


def _load_config(payload: Mapping[str, Any], case_ids: set[str]) -> CanaryConfig:
    if payload.get("schema_version") != "canary-runtime-config/v1":
        raise CanarySpecError("unsupported canary runtime config schema")
    campaign_id = _text(payload.get("campaign_id"), "campaign_id")
    model_id = _text(payload.get("model_id"), "model_id")
    if campaign_id != CANARY_CAMPAIGN_ID or model_id != CANARY_MODEL_ID:
        raise CanarySpecError("canary model or campaign is not frozen")
    provider_id = _text(payload.get("provider_id"), "provider_id")
    campaign_total_seconds = _positive_int(payload.get("campaign_total_seconds"), "campaign_total_seconds")
    case_total_seconds = _positive_int(payload.get("case_total_seconds"), "case_total_seconds")
    request_timeout_seconds = _positive_int(payload.get("request_timeout_seconds"), "request_timeout_seconds")
    sdk_attempts = _positive_int(payload.get("sdk_attempts"), "sdk_attempts")
    automatic_function_calling = payload.get("automatic_function_calling")
    thinking_level = _text(payload.get("thinking_level"), "thinking_level")
    max_input_bytes = _positive_int(payload.get("max_input_bytes"), "max_input_bytes")
    if (
        provider_id != "gemini"
        or campaign_total_seconds != 1800
        or case_total_seconds != 600
        or request_timeout_seconds != 130
        or sdk_attempts != 1
        or automatic_function_calling is not False
        or thinking_level != "LOW"
        or max_input_bytes != 49152
    ):
        raise CanarySpecError("runtime execution settings do not match the freeze")
    max_requests = _positive_int(payload.get("max_requests"), "max_requests")
    max_cost_usd = payload.get("max_cost_usd")
    if isinstance(max_cost_usd, bool) or not isinstance(max_cost_usd, (int, float)) or max_cost_usd != 3:
        raise CanarySpecError("max_cost_usd must be the frozen value 3")
    per_request = _mapping(payload.get("per_request"), "per_request")
    if per_request != {"max_input_tokens": 65536, "max_output_tokens": 8192}:
        raise CanarySpecError("per-request token limits do not match the freeze")
    frozen_per_request = MappingProxyType({name: _positive_int(value, name) for name, value in per_request.items()})
    raw_stage = _mapping(payload.get("stage_requests"), "stage_requests")
    expected_stage = {
        "F": {"C1": 2, "C2": 2, "C3": 3},
        "G": {case_id: 2 for case_id in _EXPECTED_CASE_IDS},
        "Semantic": {case_id: 1 for case_id in _EXPECTED_CASE_IDS},
        "Runtime": {case_id: 6 for case_id in _EXPECTED_CASE_IDS},
    }
    if raw_stage != expected_stage or any(
        not isinstance(values, Mapping) or set(values) != case_ids for values in raw_stage.values()
    ):
        raise CanarySpecError("stage request budget does not match the freeze")
    frozen_stage = MappingProxyType(
        {name: MappingProxyType({case_id: _positive_int(value, f"{name}.{case_id}") for case_id, value in values.items()}) for name, values in raw_stage.items()}
    )
    if sum(value for values in raw_stage.values() for value in values.values()) != max_requests:
        raise CanarySpecError("global max_requests does not equal stage request total")
    if payload.get("provider_requests_are_forbidden_in_offline_checks") is not True:
        raise CanarySpecError("offline provider prohibition must be enabled")
    return CanaryConfig(
        campaign_id=campaign_id,
        model_id=model_id,
        provider_id=provider_id,
        campaign_total_seconds=campaign_total_seconds,
        case_total_seconds=case_total_seconds,
        request_timeout_seconds=request_timeout_seconds,
        sdk_attempts=sdk_attempts,
        automatic_function_calling=False,
        thinking_level=thinking_level,
        max_input_bytes=max_input_bytes,
        max_requests=max_requests,
        max_cost_usd=max_cost_usd,
        per_request=frozen_per_request,
        stage_requests=frozen_stage,
        provider_requests_are_forbidden_in_offline_checks=True,
    )


def _load_rubric(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if payload.get("schema_version") != "canary-rubric/v2":
        raise CanarySpecError("unsupported canary rubric schema")
    if tuple(payload.get("categories", ())) != _EXPECTED_CATEGORIES:
        raise CanarySpecError("rubric categories do not match the freeze")
    if "BUSINESS_COMPLETE" in payload.get("categories", ()) or "BUSINESS_COMPLETE" not in payload.get("unsupported_categories", ()):
        raise CanarySpecError("unsupported business-complete category must remain explicit")
    if payload.get("independent_dimensions") is not True:
        raise CanarySpecError("rubric dimensions must be scored independently")
    if payload.get("refusal_alone_is_not_semantic_pass") is not True:
        raise CanarySpecError("refusal-alone rule is missing")
    if payload.get("semantic_pass_requires_source_evidence") is not True:
        raise CanarySpecError("source-evidence semantic-pass rule is missing")
    dimensions = _mapping(payload.get("dimensions"), "rubric dimensions")
    if tuple(dimensions) != _EXPECTED_DIMENSIONS:
        raise CanarySpecError("rubric dimensions do not match the freeze")
    for dimension, value in dimensions.items():
        record = _mapping(value, f"rubric dimension {dimension}")
        if record.get("max_score") != 2 or not _text(record.get("criterion"), f"rubric criterion {dimension}"):
            raise CanarySpecError(f"rubric dimension {dimension} is invalid")
    score_scale = _mapping(payload.get("score_scale"), "rubric score_scale")
    if score_scale.get("allowed_scores") != [0, 1, 2]:
        raise CanarySpecError("rubric scores must be the fixed 0/1/2 scale")
    if score_scale.get("unknown_score_forbidden") is not True or score_scale.get("empty_score_forbidden") is not True:
        raise CanarySpecError("rubric must reject empty and UNKNOWN scores")
    anchors = _mapping(score_scale.get("anchors"), "rubric score anchors")
    if tuple(anchors) != _EXPECTED_DIMENSIONS:
        raise CanarySpecError("rubric score anchors must cover every dimension")
    for dimension in _EXPECTED_DIMENSIONS:
        dimension_anchors = _mapping(anchors.get(dimension), f"rubric anchors {dimension}")
        if tuple(dimension_anchors) != ("0", "1", "2") or any(
            not isinstance(value, str) or not value.strip() for value in dimension_anchors.values()
        ):
            raise CanarySpecError(f"rubric anchors {dimension} must define non-empty 0/1/2 values")
    hard_fail_rules = payload.get("hard_fail_rules")
    if not isinstance(hard_fail_rules, list) or len(hard_fail_rules) != 1:
        raise CanarySpecError("rubric must define one all-case hard-fail rule")
    hard_fail = _mapping(hard_fail_rules[0], "rubric hard-fail rule")
    if (
        hard_fail.get("id") != "unsupported_confirmed_relation_deadline_or_completion"
        or hard_fail.get("applies_to") != "ALL_CASES"
        or hard_fail.get("category") != "CONTRACT_FAILURE"
    ):
        raise CanarySpecError("rubric hard-fail scope or category is invalid")
    trigger = _text(hard_fail.get("trigger"), "rubric hard-fail trigger")
    if not all(term in trigger for term in ("SAME_TASK", "final", "effective", "BUSINESS_COMPLETE", "trusted receipt")):
        raise CanarySpecError("rubric hard-fail trigger is incomplete")
    score_overrides = _mapping(hard_fail.get("score_overrides"), "rubric hard-fail score_overrides")
    if score_overrides != {"unconfirmed_assertions": 0}:
        raise CanarySpecError("rubric hard-fail score override is invalid")
    category_assignment = _mapping(payload.get("category_assignment"), "rubric category_assignment")
    if category_assignment.get("exactly_one_category") is not True:
        raise CanarySpecError("rubric must require exactly one category")
    if category_assignment.get("allowed_categories") != list(_EXPECTED_CATEGORIES):
        raise CanarySpecError("rubric category assignment list is invalid")
    expected_precedence = ["INFRA_FAILURE", "CONTRACT_FAILURE", "WRONG_ANSWER", "CORRECT_PARTIAL"]
    if category_assignment.get("precedence") != expected_precedence:
        raise CanarySpecError("rubric category precedence is invalid")
    category_rules = _mapping(category_assignment.get("rules"), "rubric category rules")
    if set(category_rules) != set(_EXPECTED_CATEGORIES) or any(
        not isinstance(value, str) or not value.strip() for value in category_rules.values()
    ):
        raise CanarySpecError("rubric category rules must cover every category")
    expectations = _mapping(payload.get("gold_expectations"), "gold_expectations")
    if set(expectations) != set(_EXPECTED_CASE_IDS):
        raise CanarySpecError("rubric case expectations do not match the freeze")
    return _freeze(payload)


def load_spec(package_dir: str | Path | None = None) -> CanarySpec:
    """Read and verify the frozen local package, returning immutable values.

    ``package_dir`` exists for offline tamper tests and review tooling.  It is
    still bound to the pinned manifest/artifact digests and cannot be used to
    load a different campaign without changing the explicit source freeze.
    """
    root = Path(package_dir) if package_dir is not None else _PACKAGE_DIR
    if not root.is_dir():
        raise CanarySpecError(f"canary package directory is missing: {root}")
    _, manifest_digest = _validate_manifest(root)
    sources_payload = _read_json(root, "sources.json")
    cases_payload = _read_json(root, "cases.json")
    rubric_payload = _read_json(root, "rubric.json")
    config_payload = _read_json(root, "runtime_config.json")

    if sources_payload.get("schema_version") != "canary-sources/v1":
        raise CanarySpecError("unsupported canary sources schema")
    question = _text(sources_payload.get("question"), "question")
    raw_sources = sources_payload.get("sources")
    if not isinstance(raw_sources, list) or tuple(item.get("id") for item in raw_sources if isinstance(item, Mapping)) != _EXPECTED_SOURCE_IDS:
        raise CanarySpecError("source IDs must be A, B, C, D in order")
    source_texts: dict[str, str] = {}
    source_metadata: dict[str, Mapping[str, str]] = {}
    for item in raw_sources:
        record = _mapping(item, "source")
        source_id = _text(record.get("id"), "source id")
        artifact_id = _text(record.get("artifact_id"), f"{source_id}.artifact_id")
        source_type = _text(record.get("source_type"), f"{source_id}.source_type")
        if source_type != "timestamped_text":
            raise CanarySpecError(f"{source_id} must use timestamped_text")
        text = _text(record.get("text"), f"{source_id}.text")
        source_texts[source_id] = text
        source_metadata[source_id] = MappingProxyType({"artifact_id": artifact_id, "source_type": source_type})

    cases = _load_cases(cases_payload, set(source_texts))
    config = _load_config(config_payload, set(cases))
    rubric = _load_rubric(rubric_payload)
    if config.campaign_id != CANARY_CAMPAIGN_ID:
        raise CanarySpecError("campaign identity mismatch")
    if config.stage_requests != MappingProxyType({
        name: MappingProxyType({case_id: case["stage_requests"][name] for case_id, case in cases.items()})
        for name in ("F", "G", "Semantic", "Runtime")
    }):
        raise CanarySpecError("case and global stage budgets disagree")

    frozen_sources = MappingProxyType(dict(source_texts))
    return CanarySpec(
        campaign_id=CANARY_CAMPAIGN_ID,
        model_id=CANARY_MODEL_ID,
        question=question,
        sources=frozen_sources,
        cases=cases,
        config=config,
        rubric=rubric,
        digest=manifest_digest,
        source_metadata=MappingProxyType(source_metadata),
    )


__all__ = [
    "CANARY_CAMPAIGN_ID",
    "CANARY_MODEL_ID",
    "CANARY_SPEC_VERSION",
    "EXPECTED_FREEZE_MANIFEST_SHA256",
    "EXPECTED_V1_FREEZE_MANIFEST_SHA256",
    "HISTORICAL_CANARY_SPEC_VERSION",
    "CanaryCase",
    "CanaryConfig",
    "CanarySpec",
    "CanarySpecError",
    "load_spec",
]

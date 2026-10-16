"""Offline runner for the frozen Real-Agent Contract V3 cases.

The runner is deliberately a small adapter around the existing RuntimeEngine.
It materializes each inline fixture as a temporary synthetic vault, scans it,
and then drives the production read-only tool boundary with a FakeModelAdapter.
The controller is an answer-blind deterministic fixture controller; its output
is evidence for runtime contract behavior only and is not real-model evidence.

Final cases are guarded by a repository-level exclusive claim and the frozen
fixture SHA.  They are intentionally never loaded by a development run.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if _SRC_ROOT.is_dir() and str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from linkloom.agents.claim_grounding import assess_claim_text  # noqa: E402
from linkloom.agents.model_adapter import (  # noqa: E402
    FakeModelAdapter,
    ModelAction,
    ModelProviderError,
    ModelResponse,
    ModelTurnRequest,
)
from linkloom.loader import calculate_fingerprint  # noqa: E402
from linkloom.runtime.checkpoint import InMemoryCheckpointer  # noqa: E402
from linkloom.runtime.graph import RuntimeEngine  # noqa: E402
from linkloom.runtime.models import RunRequest  # noqa: E402
from linkloom.scanner import scan_vault  # noqa: E402
from linkloom.tools.contracts import ToolCall  # noqa: E402


DATASET = "real-agent-contract-v3"
SCHEMA_VERSION = "real-agent-contract-v3-fixture/v1"
RUN_SCHEMA_VERSION = "real-agent-contract-v3-run/v1"
FIXTURE_RELATIVE = Path("tests/fixtures/real_agent_contract_v3")
FINAL_CLAIM_RELATIVE = Path("work/real-agent-contract-v3/final.claim")


class ContractV3RunnerError(RuntimeError):
    """A user-correctable runner boundary failure."""


class FinalAcceptanceRequired(ContractV3RunnerError):
    """Final partition execution was requested without explicit acceptance."""


class FinalAlreadyClaimed(ContractV3RunnerError):
    """Another final execution already owns the exclusive claim."""


class CaseNotApplicable(ContractV3RunnerError):
    """The frozen input cannot be admitted by the current production boundary."""


@dataclass(frozen=True)
class MaterializedSource:
    case_root: Path
    vault_root: Path
    index_path: Path
    index_sha256: str
    vault_root_fingerprint: str
    document_count: int

    def runtime_context(self) -> dict[str, Any]:
        # This is the exact identity shape RuntimeEngine sends to the model.
        return {
            "index_path": self.index_path.name,
            "index_sha256": self.index_sha256,
            "index_schema_version": 1,
            "vault_root_fingerprint": self.vault_root_fingerprint,
            "fixture_id": None,
            "document_count": self.document_count,
        }


@dataclass(frozen=True)
class LoadedPartition:
    repo_root: Path
    partition: str
    fixture_path: Path
    freeze_path: Path
    fixture_sha256: str
    freeze_sha256: str
    payload: dict[str, Any]
    final_claim_path: Path | None = None


@dataclass(frozen=True)
class CaseRun:
    case_id: str
    model_kind: str
    results: dict[str, Any]
    error: dict[str, Any] | None
    review: dict[str, Any] | None
    verified_reads: list[dict[str, Any]]
    budget: dict[str, Any]
    sha: dict[str, str]
    score: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "model_kind": self.model_kind,
            "results": self.results,
            "error": self.error,
            "review": self.review,
            "verified_reads": self.verified_reads,
            "budget": self.budget,
            "sha": self.sha,
            "score": self.score,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _normalize(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _safe_relative_markdown(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContractV3RunnerError("Fixture document path must be non-empty text.")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractV3RunnerError(f"Fixture document path is not relative: {value!r}")
    if path.suffix.casefold() != ".md":
        raise ContractV3RunnerError(f"Fixture document path is not Markdown: {value!r}")
    return Path(*path.parts)


def _validate_fixture_payload(payload: Mapping[str, Any], partition: str) -> None:
    required = {
        "schema_version", "dataset", "partition", "purpose", "base_commit",
        "gold_visibility", "external_blind_test", "cases",
    }
    if set(payload) != required:
        raise ContractV3RunnerError(
            f"Fixture top-level fields differ: {sorted(set(payload) ^ required)}"
        )
    if payload["schema_version"] != SCHEMA_VERSION or payload["dataset"] != DATASET:
        raise ContractV3RunnerError("Unsupported V3 fixture identity.")
    if payload["partition"] != partition or not isinstance(payload["cases"], list):
        raise ContractV3RunnerError("Fixture partition or cases field is invalid.")
    seen: set[str] = set()
    for case in payload["cases"]:
        if not isinstance(case, dict) or set(case) != {"id", "categories", "input", "stimulus", "gold"}:
            raise ContractV3RunnerError("Every V3 case must have the frozen case fields.")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ContractV3RunnerError("V3 case IDs must be unique non-empty strings.")
        seen.add(case_id)
        input_data = case["input"]
        if not isinstance(input_data, dict) or not isinstance(input_data.get("documents"), list):
            raise ContractV3RunnerError(f"{case_id}: input.documents must be a list.")
        if not input_data["documents"]:
            raise ContractV3RunnerError(f"{case_id}: input.documents must not be empty.")
        for document in input_data["documents"]:
            if not isinstance(document, dict):
                raise ContractV3RunnerError(f"{case_id}: document must be an object.")
            _safe_relative_markdown(document.get("path"))
            if not isinstance(document.get("content"), str):
                raise ContractV3RunnerError(f"{case_id}: document.content must be text.")


def _read_freeze(repo_root: Path) -> tuple[Path, dict[str, Any], str]:
    freeze_path = (repo_root / FIXTURE_RELATIVE / "freeze.json").resolve()
    if not freeze_path.is_file():
        raise ContractV3RunnerError(f"Missing V3 freeze manifest: {freeze_path}")
    try:
        payload = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractV3RunnerError("V3 freeze manifest is not valid JSON.") from error
    if payload.get("dataset") != DATASET:
        raise ContractV3RunnerError("V3 freeze manifest dataset mismatch.")
    return freeze_path, payload, _sha256_file(freeze_path)


def _claim_final(repo_root: Path, *, freeze_sha256: str) -> Path:
    claim_path = (repo_root / FINAL_CLAIM_RELATIVE).resolve()
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "schema_version": "real-agent-contract-v3-final-claim/v1",
        "dataset": DATASET,
        "partition": "final",
        "claimed_at": _utc_now(),
        "run_id": f"v3-final-{uuid4().hex}",
        "freeze_sha256": freeze_sha256,
    }
    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise FinalAlreadyClaimed(f"Final partition already claimed: {claim_path}") from error
    try:
        os.write(fd, (_canonical_json(content) + b"\n"))
    finally:
        os.close(fd)
    return claim_path


def load_partition(
    repo_root: str | Path = _REPO_ROOT,
    *,
    partition: str,
    final_acceptance: bool = False,
) -> LoadedPartition:
    """Load one frozen partition, refusing Final before the acceptance gate.

    Development loading reads only ``dev.json``.  In particular, it does not
    open or parse ``final.json`` as a source of case IDs or tuning data.
    """
    root = Path(repo_root).resolve()
    if partition not in {"dev", "final"}:
        raise ContractV3RunnerError("partition must be dev or final")
    freeze_path, freeze, freeze_sha = _read_freeze(root)
    if partition == "final" and not final_acceptance:
        raise FinalAcceptanceRequired(
            "Final partition requires --final-acceptance after repair confirmation."
        )
    if partition == "final":
        claim_path = _claim_final(root, freeze_sha256=freeze_sha)
    else:
        claim_path = None

    fixture_path = (root / FIXTURE_RELATIVE / f"{partition}.json").resolve()
    expected = (
        freeze.get("partitions", {}).get(partition, {}).get("sha256")
        or freeze.get("frozen_content_files", {}).get(
            f"tests/fixtures/real_agent_contract_v3/{partition}.json"
        )
    )
    actual = _sha256_file(fixture_path)
    if not isinstance(expected, str) or actual != expected:
        raise ContractV3RunnerError(
            f"{partition} fixture SHA does not match freeze manifest: {actual}"
        )
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContractV3RunnerError(f"Invalid V3 {partition} fixture JSON.") from error
    _validate_fixture_payload(payload, partition)
    expected_count = freeze.get("partitions", {}).get(partition, {}).get("case_count")
    if expected_count is not None and len(payload["cases"]) != expected_count:
        raise ContractV3RunnerError("V3 fixture case count differs from freeze manifest.")
    return LoadedPartition(
        repo_root=root,
        partition=partition,
        fixture_path=fixture_path,
        freeze_path=freeze_path,
        fixture_sha256=actual,
        freeze_sha256=freeze_sha,
        payload=payload,
        final_claim_path=claim_path,
    )


def model_input_projection(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the public input projection used to configure an offline case.

    Gold and the fixture's logical source identity are intentionally excluded.
    The runtime later supplies a real scanner-derived source context.
    """
    input_data = case.get("input")
    if not isinstance(input_data, Mapping):
        raise ContractV3RunnerError("case.input must be an object")
    allowed = {"workspace_id", "query", "documents", "budget", "task_plan"}
    projected = {key: deepcopy(input_data[key]) for key in allowed if key in input_data}
    stimulus = case.get("stimulus")
    if isinstance(stimulus, Mapping):
        # Keep only the explicitly documented offline failure controls.  Tool
        # source_context examples are logical fixture values and are replaced
        # by the controller before a ToolCall reaches production runtime.
        projected["offline_stimulus"] = {
            key: deepcopy(stimulus[key])
            for key in ("mode", "provider_outcome", "uncertain_reason", "context_pressure")
            if key in stimulus
        }
    return projected


def materialize_case(
    case: Mapping[str, Any],
    output_dir: str | Path,
) -> MaterializedSource:
    """Write a synthetic fixture and materialize scanner-owned identity."""
    case_id = str(case.get("id", "case"))
    input_data = case.get("input")
    if not isinstance(input_data, Mapping):
        raise ContractV3RunnerError(f"{case_id}: input must be an object")
    case_root = Path(output_dir).resolve() / ".cases" / case_id
    vault_root = case_root / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    documents = input_data.get("documents")
    if not isinstance(documents, list):
        raise ContractV3RunnerError(f"{case_id}: documents must be a list")
    for document in documents:
        if not isinstance(document, Mapping):
            raise ContractV3RunnerError(f"{case_id}: document must be an object")
        path = _safe_relative_markdown(document.get("path"))
        target = vault_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(document.get("content", "")), encoding="utf-8")
    scan = scan_vault(vault_root, case_root / "scan")
    index_sha = _sha256_file(scan.index_path)
    return MaterializedSource(
        case_root=case_root,
        vault_root=vault_root,
        index_path=scan.index_path,
        index_sha256=index_sha,
        vault_root_fingerprint=calculate_fingerprint(vault_root),
        document_count=scan.note_count,
    )


def _request_source_context(request: ModelTurnRequest) -> dict[str, Any]:
    packet = _request_packet(request)
    source = packet.get("source_context")
    if not isinstance(source, dict):
        raise ContractV3RunnerError("Runtime request has no source_context.")
    return deepcopy(source)


def _request_packet(request: ModelTurnRequest) -> dict[str, Any]:
    marker = "team_decision_request="
    if marker not in request.user_input:
        raise ContractV3RunnerError("Runtime request did not expose team_decision_request.")
    try:
        packet = json.loads(request.user_input.split(marker, 1)[1])
    except (ValueError, TypeError) as error:
        raise ContractV3RunnerError("Runtime team_decision_request is not JSON.") from error
    if not isinstance(packet, dict):
        raise ContractV3RunnerError("Runtime team_decision_request must be an object.")
    return packet


def _safe_tool_call(request: ModelTurnRequest, tool_id: str, arguments: dict[str, Any]) -> ModelAction:
    return ModelAction.tool(
        ToolCall(
            call_id=f"v3-controller-{request.sequence}",
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _compact(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _select_evidence(results: Sequence[Mapping[str, Any]], note_ref: str, used: set[str]) -> Mapping[str, Any] | None:
    marker = _compact(note_ref)
    candidates = [item for item in results if isinstance(item, Mapping) and item.get("evidence_id") not in used]
    for item in candidates:
        path = str(item.get("relative_path", ""))
        if _compact(path) and _compact(path) in marker:
            return item
    return candidates[0] if candidates else (results[0] if results else None)


def _parse_claims(reads: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract only relations present in observed verified passages."""
    relation_names = {
        "owner_of": "owner_of_project",
        "direct_leader_of": "reports_to",
        "reports_to": "reports_to",
        "alias_of": "alias_of_project",
    }
    found: list[dict[str, Any]] = []
    for evidence in reads:
        ref = evidence.get("evidence_id")
        quote = evidence.get("quote")
        if not isinstance(ref, str) or not isinstance(quote, str):
            continue
        clauses = [part.strip().lstrip("# ") for part in re.split(r"\r?\n+|(?<=[.!?。？！])\s*", quote) if part.strip()]
        by_ref = {ref: evidence}
        for clause in clauses:
            assessment = assess_claim_text(clause, [ref], by_ref)
            for claim in assessment.claims:
                predicate = relation_names.get(str(claim.relation))
                if predicate is None:
                    continue
                def preserve(value: Any) -> str:
                    raw = str(value)
                    match = re.search(re.escape(raw), clause, flags=re.IGNORECASE)
                    return match.group(0) if match else raw
                row = {
                    "predicate": predicate,
                    "subject": preserve(claim.subject),
                    "object": preserve(claim.value),
                    "evidence_id": ref,
                    "quote": clause,
                }
                if row not in found:
                    found.append(row)
    return found


def _claim_text(claim: Mapping[str, Any]) -> str:
    subject = str(claim["subject"])
    value = str(claim["object"])
    if any(ord(char) > 127 for char in subject + value):
        if claim["predicate"] == "owner_of_project":
            return f"{subject}的负责人是{value}"
        if claim["predicate"] == "reports_to":
            return f"{subject}的直属领导是{value}"
        return f"{subject}是{value}的别名"
    if claim["predicate"] == "owner_of_project":
        return f"{value} is the accountable owner of {subject}"
    if claim["predicate"] == "reports_to":
        return f"{subject} reports directly to {value}"
    return f"{subject} is the team alias for {value}"


def _final_payload(variant: str, reads: Sequence[Mapping[str, Any]]) -> str:
    claims = _parse_claims(reads)
    # Keep source order while avoiding duplicate same-passage claims.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for claim in claims:
        identity = (claim["predicate"], _normalize(claim["subject"]), _normalize(claim["object"]))
        if identity not in seen:
            seen.add(identity)
            unique.append(claim)
    refs = list(dict.fromkeys(str(item["evidence_id"]) for item in unique))
    if variant == "valid_partial_owner_only":
        # A partial TeamDecisionResult has no material decision value.  The
        # verified owner is retained as grounded rationale while the missing
        # leader stays explicitly unknown.
        status = "insufficient_evidence"
        uncertainty = "insufficient_evidence"
        unknown = ["direct_leader"]
        value = None
        rationale = [
            {"point": _claim_text(item), "evidence_refs": [item["evidence_id"]]}
            for item in unique
        ]
        decision_refs: list[str] = []
        uncertainty_refs: list[str] = []
    elif variant == "valid_conflict":
        # Conflict keeps both opposing grounded claims and makes the unresolved
        # owner slot explicit; it never chooses a winner.
        status = "insufficient_evidence"
        uncertainty = "conflicting_evidence"
        unknown = ["owner"]
        value = None
        rationale = [
            {"point": _claim_text(item), "evidence_refs": [item["evidence_id"]]}
            for item in unique
        ]
        decision_refs = []
        uncertainty_refs = refs
    else:
        unknown = []
        status = "approved"
        uncertainty = "none"
        value = "; ".join(_claim_text(item) for item in unique) or None
        rationale = []
        decision_refs = refs
        uncertainty_refs = []
    payload = {
        "schema_version": "team-decision-result/v1",
        "decision": {"value": value, "status": status, "evidence_refs": decision_refs},
        "rationale": rationale,
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": [],
        "uncertainty": {
            "status": uncertainty,
            "statement": None,
            "unknown_fields": unknown,
            "evidence_refs": uncertainty_refs,
        },
        "evidence_refs": refs,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class V3FixtureController:
    """Public-document/observation-only scripted model controller."""

    def __init__(self, case: Mapping[str, Any]) -> None:
        self.case_id = str(case["id"])
        self.input_data = deepcopy(case["input"])
        self.stimulus = deepcopy(case["stimulus"])
        self.tool_specs = list(self.stimulus.get("tool_calls", []))
        self.tool_index = 0
        self.search_results: list[Mapping[str, Any]] = []
        self.reads: list[Mapping[str, Any]] = []
        self.used_refs: set[str] = set()
        self.requests: list[ModelTurnRequest] = []
        self.search_calls = 0
        self._known_entities: dict[str, str] = {}

    def _source_context(self, request: ModelTurnRequest, spec: Mapping[str, Any]) -> dict[str, Any]:
        # Start from Host-owned context.  Fixture index IDs and pseudo SHAs are
        # never copied.  Only task-plan extension fields are model-supplied.
        source = _request_source_context(request)
        raw = spec.get("arguments", {}).get("source_context", {})
        if not isinstance(raw, Mapping):
            raw = {}
        for key in ("scope_path", "subgoal_id", "dependency_entities", "query_variant"):
            if key in raw:
                source[key] = deepcopy(raw[key])
        # Runtime auto-plans common owner/leader questions.  The planner owns
        # the subgoal identity; the fixture only chooses the corresponding
        # scripted query and never supplies an index identity.
        if "subgoal_id" not in source:
            packet = _request_packet(request)
            plan = packet.get("task_plan")
            subgoals = plan.get("subgoals", []) if isinstance(plan, Mapping) else []
            if self.search_calls < len(subgoals) and isinstance(subgoals[self.search_calls], Mapping):
                subgoal = subgoals[self.search_calls]
                if isinstance(subgoal.get("subgoal_id"), str):
                    source["subgoal_id"] = subgoal["subgoal_id"]
                    source["dependency_entities"] = {
                        str(parent): self._known_entities.get(str(parent), "")
                        for parent in subgoal.get("depends_on", [])
                    }
        # D06's first call intentionally tests a Host-owned mismatch.  Mutate
        # the real Runtime identity, never inject the fixture pseudo identity.
        if self.case_id == "RAC3-D06" and self.tool_index == 0:
            source["vault_root_fingerprint"] = "root_wrong_workspace"
        return source

    def __call__(self, request: ModelTurnRequest) -> ModelAction:
        self.requests.append(request)
        observation = request.observation
        if observation is not None and observation.status == "ok":
            if observation.tool_id == "search_notes" and isinstance(observation.value, list):
                self.search_results = [item for item in observation.value if isinstance(item, Mapping)]
            elif observation.tool_id == "read_verified_note" and isinstance(observation.value, Mapping):
                self.reads.append(dict(observation.value))
                if isinstance(observation.value.get("evidence_id"), str):
                    self.used_refs.add(observation.value["evidence_id"])

        if self.tool_index < len(self.tool_specs):
            spec = self.tool_specs[self.tool_index]
            self.tool_index += 1
            if not isinstance(spec, Mapping):
                raise ContractV3RunnerError(f"{self.case_id}: malformed stimulus tool call")
            tool_id = spec.get("tool_id")
            raw_args = deepcopy(spec.get("arguments", {}))
            if not isinstance(tool_id, str) or not isinstance(raw_args, dict):
                raise ContractV3RunnerError(f"{self.case_id}: malformed stimulus tool call")
            if tool_id == "search_notes":
                raw_args["source_context"] = self._source_context(request, spec)
                self.search_calls += 1
            elif tool_id == "read_verified_note":
                note_ref = raw_args.get("note_ref")
                if self.case_id != "RAC3-D06":
                    selected = _select_evidence(self.search_results, str(note_ref), self.used_refs)
                    if selected is not None:
                        raw_args["note_ref"] = selected.get("evidence_id")
            return _safe_tool_call(request, tool_id, raw_args)

        variant = str(self.stimulus.get("final", {}).get("variant", "valid_complete"))
        if variant == "not_received_after_max_steps" and self.search_results and not self.reads:
            # The budget fixture intentionally allows Search + Read but not a
            # third model turn for Final.  Consume the observed search result;
            # Runtime owns the terminal budget decision.
            selected = _select_evidence(self.search_results, "", self.used_refs)
            if selected is not None:
                return _safe_tool_call(
                    request,
                    "read_verified_note",
                    {"note_ref": selected.get("evidence_id")},
                )
        if variant == "missing_required_uncertainty_field":
            payload = json.loads(_final_payload("valid_complete", self.reads))
            payload["uncertainty"].pop("unknown_fields", None)
            return ModelAction.final(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        if variant == "not_received_after_max_steps" or variant == "none_after_invalid_tool_calls":
            # The configured budget/invalid-call guard should terminate before
            # this branch.  A safe fallback keeps the controller answer-blind.
            return ModelAction.final(_final_payload("valid_partial_owner_only", self.reads))
        return ModelAction.final(_final_payload(variant, self.reads))


class UncertainFakeModelAdapter(FakeModelAdapter):
    """Fake adapter that returns one explicit terminal Provider uncertainty."""

    def __init__(self) -> None:
        super().__init__([ModelAction.final("unused")])

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
        self.requests.append(request)
        self._index += 1
        return ModelResponse(
            error=ModelProviderError(
                code="MODEL_UNKNOWN_FAILURE",
                category="unknown_provider_error",
                message="Synthetic Provider outcome is uncertain.",
                retryable=False,
                outcome="unknown_provider_outcome",
                details={"reason": "scripted_fixture_uncertain"},
            )
        )


def _case_budget(case: Mapping[str, Any]) -> dict[str, Any]:
    raw = case.get("input", {}).get("budget", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def _build_model(case: Mapping[str, Any]) -> tuple[FakeModelAdapter, str, V3FixtureController | None]:
    stimulus = case.get("stimulus", {})
    if isinstance(stimulus, Mapping) and stimulus.get("provider_outcome") == "uncertain":
        return UncertainFakeModelAdapter(), "FakeModelAdapter/ProviderOutcomeUncertain", None
    controller = V3FixtureController(case)
    # One callable is sufficient: FakeModelAdapter invokes it once per model
    # turn and the controller advances only through observed runtime state.
    return FakeModelAdapter([controller] * 32), "FakeModelAdapter/V3FixtureController", controller


def _result_payload(root: Path, status: Any, engine: RuntimeEngine) -> dict[str, Any]:
    result_path = engine.checkpoint_dir / "results" / status.run_id / "result.json"
    if not result_path.is_file():
        return {}
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _state_error(status: Any, state: Any | None) -> dict[str, Any] | None:
    error = getattr(status, "error", None)
    if isinstance(error, dict):
        return error
    state_error = getattr(state, "error", None)
    return state_error.to_dict() if state_error is not None else None


def _verified_reads(state: Any | None) -> list[dict[str, Any]]:
    if state is None:
        return []
    rows: list[dict[str, Any]] = []
    for record in getattr(state, "tool_ledger", []):
        if record.tool_id != "read_verified_note" or record.status != "completed":
            continue
        value = record.result.get("value") if isinstance(record.result, dict) else None
        if isinstance(value, dict) and value.get("status") == "verified":
            rows.append(deepcopy(value))
    return rows


def _actual_facts(result_payload: Mapping[str, Any], reads: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    final = result_payload.get("result", {}).get("team_decision") if isinstance(result_payload.get("result"), Mapping) else None
    if not isinstance(final, Mapping):
        return [
            (str(row["predicate"]), _normalize(row["subject"]), _normalize(row["object"]))
            for row in _parse_claims(reads)
        ]
    texts: list[str] = []
    value = final.get("decision", {}).get("value") if isinstance(final.get("decision"), Mapping) else None
    if isinstance(value, str):
        texts.append(value)
    for item in final.get("rationale", []) if isinstance(final.get("rationale"), list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("point"), str):
            texts.append(item["point"])
    by_ref = {str(item.get("evidence_id")): item for item in reads if item.get("evidence_id")}
    found: list[tuple[str, str, str]] = []
    for text in texts:
        refs = list(by_ref)
        assessment = assess_claim_text(text, refs, by_ref)
        mapping = {"owner_of": "owner_of_project", "direct_leader_of": "reports_to", "reports_to": "reports_to", "alias_of": "alias_of_project"}
        for claim in assessment.claims:
            predicate = mapping.get(str(claim.relation))
            if predicate:
                row = (predicate, _normalize(claim.subject), _normalize(claim.value))
                if row not in found:
                    found.append(row)
    return found


def _expected_facts(gold: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    rows = gold.get("facts", [])
    if not isinstance(rows, list):
        return []
    return [
        (_normalize(row.get("predicate")), _normalize(row.get("subject")), _normalize(row.get("object")))
        for row in rows if isinstance(row, Mapping)
    ]


def _actual_business_status(
    result_payload: Mapping[str, Any],
    error: Mapping[str, Any] | None,
    reads: Sequence[Mapping[str, Any]] = (),
) -> str:
    final = result_payload.get("result", {}).get("team_decision") if isinstance(result_payload.get("result"), Mapping) else None
    if isinstance(final, Mapping):
        decision = final.get("decision") if isinstance(final.get("decision"), Mapping) else {}
        uncertainty = final.get("uncertainty") if isinstance(final.get("uncertainty"), Mapping) else {}
        if uncertainty.get("status") == "conflicting_evidence":
            return "conflict"
        if decision.get("status") == "partial":
            return "partial"
        if decision.get("status") in {"insufficient_evidence", "not_found"}:
            rationale = final.get("rationale")
            if isinstance(rationale, list) and rationale:
                return "partial"
        if decision.get("status") == "approved":
            return "complete"
    if error and error.get("details", {}).get("outcome") == "unknown_provider_outcome":
        return "unknown"
    return "incomplete"


def _actual_runtime_status(
    result_payload: Mapping[str, Any],
    error: Mapping[str, Any] | None,
    state: Any | None,
) -> str:
    """Map durable runtime details to the fixture's business-facing status."""
    contract = _actual_final_contract(result_payload, error)
    if contract == "rejected":
        return "rejected"
    details = error.get("details", {}) if isinstance(error, Mapping) else {}
    if not isinstance(details, Mapping):
        details = {}
    outcome = details.get("outcome")
    if outcome == "unknown_provider_outcome":
        return "provider_uncertain"
    termination = getattr(state, "termination", None)
    if termination is not None and getattr(termination, "status", None) == "budget_exhausted":
        return "budget_exhausted"
    code = str(error.get("code")) if isinstance(error, Mapping) else ""
    if code in {"MODEL_PROVIDER_BUDGET_EXHAUSTED", "MODEL_STEP_BUDGET_EXHAUSTED", "MODEL_MAX_STEPS_EXCEEDED"}:
        return "budget_exhausted"
    if code in {"AGENT_WORKFLOW_FAILED", "AGENT_RUNTIME_ERROR"}:
        ledger = getattr(state, "tool_ledger", []) if state is not None else []
        if any(getattr(record, "status", None) == "failed" for record in ledger):
            return "safe_error"
    if getattr(state, "status", None) == "completed":
        return "completed"
    return str(getattr(state, "status", "failed"))


def _actual_final_contract(result_payload: Mapping[str, Any], error: Mapping[str, Any] | None) -> str:
    final = result_payload.get("result", {}).get("team_decision") if isinstance(result_payload.get("result"), Mapping) else None
    if isinstance(final, Mapping):
        return "accepted"
    if error and error.get("code") in {"MODEL_FINAL_CORRECTION_EXHAUSTED", "MODEL_SCHEMA_CORRECTION_EXHAUSTED", "TEAM_DECISION_CONTRACT_ERROR"}:
        return "rejected"
    return "not_received"


def _actual_grounding(result_payload: Mapping[str, Any]) -> str:
    final = result_payload.get("result", {}).get("team_decision") if isinstance(result_payload.get("result"), Mapping) else None
    return "pass" if isinstance(final, Mapping) else "not_run"


def _actual_reviewer(result_payload: Mapping[str, Any]) -> str:
    review = result_payload.get("review")
    if not isinstance(review, Mapping):
        return "not_run"
    decision = review.get("decision")
    return {"evidence_sufficient": "pass", "evidence_insufficient": "fail", "schema_invalid": "fail"}.get(str(decision), str(decision))


def _observed_paths(state: Any | None) -> list[str]:
    """Return paths visible in successful search/read observations."""
    if state is None:
        return []
    paths: list[str] = []
    for record in getattr(state, "tool_ledger", []):
        if record.status != "completed" or not isinstance(record.result, dict):
            continue
        value = record.result.get("value")
        rows = value if record.tool_id == "search_notes" and isinstance(value, list) else [value]
        for row in rows:
            if isinstance(row, Mapping) and isinstance(row.get("relative_path"), str):
                if row["relative_path"] not in paths:
                    paths.append(row["relative_path"])
    return paths


def _guard_results(case: Mapping[str, Any], *, result_payload: Mapping[str, Any], error: Mapping[str, Any] | None, reads: Sequence[Mapping[str, Any]], state: Any | None) -> dict[str, Any]:
    case_id = str(case.get("id"))
    facts = _actual_facts(result_payload, reads)
    statuses = [getattr(record, "status", "") for record in getattr(state, "model_executions", [])] if state is not None else []
    read_paths = {str(item.get("relative_path")) for item in reads}
    final = result_payload.get("result", {}).get("team_decision") if isinstance(result_payload.get("result"), Mapping) else None
    uncertainty = final.get("uncertainty", {}) if isinstance(final, Mapping) and isinstance(final.get("uncertainty"), Mapping) else {}
    guards = {
        "invalid_final_is_not_projected_as_success": case_id != "RAC3-D01" or _actual_final_contract(result_payload, error) == "rejected",
        "observed_evidence_is_retained_for_diagnosis": case_id != "RAC3-D01" or bool(reads),
        "claims_use_observed_verified_refs": not isinstance(final, Mapping) or set(final.get("evidence_refs", [])) <= {str(item.get("evidence_id")) for item in reads},
        "verified_owner_is_retained": case_id != "RAC3-D03" or any(row[0] == "owner_of_project" for row in facts),
        "missing_leader_is_unknown": case_id != "RAC3-D03" or ("direct_leader" in uncertainty.get("unknown_fields", []) and not any(row[0] == "reports_to" for row in facts)),
        "partial_is_not_failure": case_id != "RAC3-D03" or (_actual_business_status(result_payload, error, reads) == "partial" and getattr(state, "status", None) == "completed"),
        "both_conflicting_refs_are_preserved": case_id != "RAC3-D04" or len(reads) >= 2,
        "conflict_is_explicit": case_id != "RAC3-D04" or uncertainty.get("status") == "conflicting_evidence",
        "no_winner_is_selected": case_id != "RAC3-D04" or _actual_business_status(result_payload, error, reads) == "conflict",
        "entity_boundary_is_exact": case_id != "RAC3-D05" or any("星河港" in row[1] for row in facts) and not any("星河港口" in row[1] for row in facts),
        "quoted_chinese_passage_supports_claims": case_id != "RAC3-D05" or bool(facts) and len(reads) == 1,
        "host_source_identity_is_authoritative": case_id != "RAC3-D06" or not any(
            record.status == "completed" for record in getattr(state, "tool_ledger", [])
        ),
        "unverified_dependency_rejected": case_id != "RAC3-D06" or any(
            record.status == "failed" and record.tool_id == "search_notes"
            for record in getattr(state, "tool_ledger", [])
        ),
        "repeated_invalid_call_stops_recovery": case_id != "RAC3-D06" or len(
            getattr(state, "model_executions", [])
        ) <= 3,
        "executor_suppressed": case_id != "RAC3-D06" or not any(record.status == "completed" for record in getattr(state, "tool_ledger", [])),
        "same_file_passages_remain_addressable": case_id != "RAC3-D07" or bool(reads),
        "distinct_passages_are_not_collapsed_into_one_claim": case_id != "RAC3-D07" or len(facts) >= 2,
        "unrelated_text_is_not_claimed": case_id != "RAC3-D07" or not any("migration" in row[2] for row in facts),
        "required_verified_reads_survive_context_budget": case_id != "RAC3-D08" or len(reads) >= 2,
        "selected_context_is_workspace_scoped": case_id != "RAC3-D08" or all(
            item.get("workspace_id") in {None, getattr(state.source, "vault_root_fingerprint", None)}
            for item in reads
        ),
        "budget_is_terminal": case_id != "RAC3-D09" or (
            (getattr(state, "termination", None) is not None and state.termination.status == "budget_exhausted")
            or (isinstance(error, Mapping) and error.get("code") in {
                "MODEL_MAX_STEPS_EXCEEDED", "MODEL_PROVIDER_BUDGET_EXHAUSTED", "MODEL_STEP_BUDGET_EXHAUSTED",
            })
        ),
        "ledger_counts_the_search": case_id != "RAC3-D09" or any(record.tool_id == "search_notes" for record in getattr(state, "tool_ledger", [])),
        "partial_evidence_is_not_projected_as_final": case_id != "RAC3-D09" or not isinstance(final, Mapping),
        "budget_failure_is_not_insufficient_evidence": case_id != "RAC3-D09" or _actual_business_status(result_payload, error, reads) == "incomplete",
        "uncertain_outcome_is_terminal_for_this_batch": case_id != "RAC3-D10" or bool(statuses) and any(record == "failed" for record in statuses),
        "ledger_reservation_is_append_only": case_id != "RAC3-D10" or len(statuses) == 1,
        "no_automatic_resend": case_id != "RAC3-D10" or len(statuses) <= 1,
        "no_unverified_final": case_id != "RAC3-D10" or not isinstance(final, Mapping),
        "reviewer_pass_is_not_human_approval": not isinstance(result_payload.get("review"), Mapping) or result_payload["review"].get("human_approval_required") is False,
    }
    if case_id == "RAC3-D08":
        guards["dropped_noise_is_audited"] = "ember-ridge-noise.md" not in read_paths
    owners = {
        "invalid_final_is_not_projected_as_success": "RAC3-D01",
        "observed_evidence_is_retained_for_diagnosis": "RAC3-D01",
        "verified_owner_is_retained": "RAC3-D03",
        "missing_leader_is_unknown": "RAC3-D03",
        "partial_is_not_failure": "RAC3-D03",
        "both_conflicting_refs_are_preserved": "RAC3-D04",
        "conflict_is_explicit": "RAC3-D04",
        "no_winner_is_selected": "RAC3-D04",
        "entity_boundary_is_exact": "RAC3-D05",
        "quoted_chinese_passage_supports_claims": "RAC3-D05",
        "host_source_identity_is_authoritative": "RAC3-D06",
        "unverified_dependency_rejected": "RAC3-D06",
        "repeated_invalid_call_stops_recovery": "RAC3-D06",
        "executor_suppressed": "RAC3-D06",
        "same_file_passages_remain_addressable": "RAC3-D07",
        "distinct_passages_are_not_collapsed_into_one_claim": "RAC3-D07",
        "unrelated_text_is_not_claimed": "RAC3-D07",
        "required_verified_reads_survive_context_budget": "RAC3-D08",
        "dropped_noise_is_audited": "RAC3-D08",
        "selected_context_is_workspace_scoped": "RAC3-D08",
        "budget_is_terminal": "RAC3-D09",
        "ledger_counts_the_search": "RAC3-D09",
        "partial_evidence_is_not_projected_as_final": "RAC3-D09",
        "budget_failure_is_not_insufficient_evidence": "RAC3-D09",
        "uncertain_outcome_is_terminal_for_this_batch": "RAC3-D10",
        "ledger_reservation_is_append_only": "RAC3-D10",
        "no_automatic_resend": "RAC3-D10",
        "no_unverified_final": "RAC3-D10",
    }
    for name, owner in owners.items():
        if case_id != owner:
            guards[name] = "not_observed"
    if not isinstance(final, Mapping):
        guards["claims_use_observed_verified_refs"] = "not_observed"
    if not isinstance(result_payload.get("review"), Mapping):
        guards["reviewer_pass_is_not_human_approval"] = "not_observed"
    guards["ledger_reservation_is_append_only"] = "not_observed"
    guards["required_verified_reads_survive_context_budget"] = "not_observed"
    return guards


def score_case(case: Mapping[str, Any], *, result_payload: Mapping[str, Any], error: Mapping[str, Any] | None, reads: Sequence[Mapping[str, Any]], state: Any | None) -> dict[str, Any]:
    """Compare the observed Runtime result with this case's author-visible Gold."""
    gold = case.get("gold")
    if not isinstance(gold, Mapping):
        raise ContractV3RunnerError(f"{case.get('id')}: missing Gold")
    actual = {
        "business_status": _actual_business_status(result_payload, error, reads),
        "runtime_status": _actual_runtime_status(result_payload, error, state),
        "final_contract": _actual_final_contract(result_payload, error),
        "grounding": _actual_grounding(result_payload),
        "reviewer": _actual_reviewer(result_payload),
        "evidence_paths": _observed_paths(state),
        "facts": [list(row) for row in _actual_facts(result_payload, reads)],
    }
    expected = {
        key: gold.get(key)
        for key in ("business_status", "runtime_status", "final_contract", "grounding", "reviewer", "evidence_paths")
    }
    # Runtime status in Gold describes the expected terminal status.  Result
    # error details are retained separately for budget/provider diagnosis.
    mismatches: dict[str, dict[str, Any]] = {}
    for key, expected_value in expected.items():
        if key == "evidence_paths":
            if set(actual[key]) != set(expected_value or []):
                mismatches[key] = {"expected": expected_value, "actual": actual[key]}
        elif expected_value is not None and actual[key] != expected_value:
            mismatches[key] = {"expected": expected_value, "actual": actual[key]}
    expected_facts = _expected_facts(gold)
    if set(map(tuple, actual["facts"])) != set(expected_facts):
        mismatches["facts"] = {"expected": [list(row) for row in expected_facts], "actual": actual["facts"]}
    guards = _guard_results(case, result_payload=result_payload, error=error, reads=reads, state=state)
    required = [str(item) for item in gold.get("required_guards", []) if isinstance(item, str)]
    guard_failures = [item for item in required if guards.get(item) is False]
    guard_na = [item for item in required if guards.get(item) is not True and guards.get(item) is not False]
    if guard_failures or guard_na:
        mismatches["required_guards"] = {
            "expected": required,
            "failed": guard_failures,
            "not_observed": guard_na,
        }
    has_mismatch = any(bool(value) for value in mismatches.values())
    passed = not has_mismatch
    qualification = "PASS" if passed else "N/A" if not guard_failures and guard_na and len(mismatches) == 1 else "FAIL"
    ambiguity_reason = None
    if (
        expected.get("business_status") == "complete"
        and expected.get("runtime_status") == "rejected"
        and actual.get("runtime_status") == "rejected"
    ):
        # The frozen Gold intentionally separates business evidence from the
        # rejected provider Final.  Do not coerce that metric disagreement
        # into either accepted success or a runtime regression.
        qualification = "N/A"
        ambiguity_reason = "Gold business completeness and rejected Final are separate metrics; no single qualification claim is made."
    return {
        "passed": passed,
        "qualification": qualification,
        **({"qualification_reason": ambiguity_reason} if ambiguity_reason else {}),
        "expected": {**expected, "facts": [list(row) for row in expected_facts], "required_guards": required},
        "actual": actual,
        "guards": guards,
        "mismatches": {key: value for key, value in mismatches.items() if value},
        "gold_read_after_execution": True,
    }


def _run_one_case(case: Mapping[str, Any], output_dir: Path) -> CaseRun:
    case_id = str(case["id"])
    source = materialize_case(case, output_dir)
    model, model_kind, controller = _build_model(case)
    budget = _case_budget(case)
    runtime_root = source.case_root / "runtime"
    engine = RuntimeEngine(
        vault_root=source.vault_root,
        index_path=source.index_path,
        checkpoint_dir=runtime_root,
        trace_dir=source.case_root / "traces",
        checkpointer=InMemoryCheckpointer(),
        model=model,
        verified_evidence_context_limit=(2 if case_id == "RAC3-D08" else 8),
        decision_memory_enabled=False,
    )
    input_data = case["input"]
    task_plan = input_data.get("task_plan")
    if isinstance(task_plan, Mapping) and task_plan.get("goal") != input_data.get("query"):
        reason = "Frozen TaskPlan goal differs from RunRequest query; production admission rejects this case."
        return CaseRun(
            case_id=case_id,
            model_kind="N/A/ProductionBoundary",
            results={"status": "not_run", "reason": reason},
            error={"code": "CASE_NOT_APPLICABLE", "message": reason},
            review=None,
            verified_reads=[],
            budget={**budget, "model_turns": 0, "provider_requests": 0, "tool_calls": 0, "termination": None},
            sha={
                "fixture_input_sha256": _sha256_bytes(_canonical_json(model_input_projection(case))),
                "materialized_index_sha256": source.index_sha256,
                "vault_root_fingerprint": source.vault_root_fingerprint,
            },
            score={
                "passed": False,
                "qualification": "N/A",
                "reason": reason,
                "gold_read_after_execution": False,
            },
        )
    request = RunRequest(
        request_id=f"v3-{case_id.lower()}",
        thread_id=f"v3-{case_id.lower()}",
        workflow="team_decision",
        query=str(input_data["query"]),
        task_plan=deepcopy(task_plan),
        max_steps=int(budget.get("max_steps", 8)),
        max_provider_requests=int(budget.get("max_provider_requests", 1)),
        dry_run=True,
    )
    # Keep the offline run deterministic and dependency-free while retaining
    # the production RuntimeAgentAdapter/tool boundary.  BM25 is an existing
    # runtime retrieval mode; no fixture answer or identity is injected.
    previous_retrieval_mode = os.environ.get("LINKLOOM_RETRIEVAL_MODE")
    os.environ["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    try:
        status = engine.start_multi_agent(request)
    finally:
        if previous_retrieval_mode is None:
            os.environ.pop("LINKLOOM_RETRIEVAL_MODE", None)
        else:
            os.environ["LINKLOOM_RETRIEVAL_MODE"] = previous_retrieval_mode
    state = engine.checkpointer.get_latest(status.thread_id)
    payload = _result_payload(source.case_root, status, engine)
    error = _state_error(status, state)
    reads = _verified_reads(state)
    score = score_case(case, result_payload=payload, error=error, reads=reads, state=state)
    model_requests = getattr(model, "requests", [])
    model_executions = getattr(state, "model_executions", []) if state is not None else []
    review = payload.get("review") if isinstance(payload.get("review"), dict) else None
    configured_budget = dict(budget)
    budget_result = {
        **configured_budget,
        "model_turns": len(model_requests),
        "provider_requests": len(model_executions),
        "tool_calls": len(getattr(state, "tool_ledger", [])) if state is not None else 0,
        "termination": state.termination.to_dict() if state is not None and state.termination else None,
    }
    projected = model_input_projection(case)
    sha = {
        "fixture_input_sha256": _sha256_bytes(_canonical_json(projected)),
        "materialized_index_sha256": source.index_sha256,
        "vault_root_fingerprint": source.vault_root_fingerprint,
    }
    results = {
        "run_id": status.run_id,
        "thread_id": status.thread_id,
        "status": status.status,
        "payload": payload,
    }
    return CaseRun(
        case_id=case_id,
        model_kind=model_kind,
        results=results,
        error=error,
        review=review,
        verified_reads=reads,
        budget=budget_result,
        sha=sha,
        score=score,
    )


def _resolve_output(repo_root: Path, value: str | Path) -> Path:
    if str(value).casefold() == "unique":
        return (repo_root / "work" / "real-agent-contract-v3" / f"run-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}").resolve()
    return Path(value).resolve() if Path(value).is_absolute() else (repo_root / Path(value)).resolve()


def run_partition(
    *,
    repo_root: str | Path = _REPO_ROOT,
    partition: str,
    output: str | Path,
    final_acceptance: bool = False,
) -> dict[str, Any]:
    loaded = load_partition(repo_root, partition=partition, final_acceptance=final_acceptance)
    output_dir = _resolve_output(loaded.repo_root, output)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = _utc_now()
    case_runs: list[CaseRun] = []
    for case in loaded.payload["cases"]:
        try:
            case_runs.append(_run_one_case(case, output_dir))
        except Exception as error:
            case_id = str(case.get("id", "UNKNOWN"))
            case_runs.append(CaseRun(
                case_id=case_id,
                model_kind="FakeModelAdapter/V3FixtureController",
                results={},
                error={"code": type(error).__name__, "message": str(error)},
                review=None,
                verified_reads=[],
                budget=_case_budget(case),
                sha={"fixture_input_sha256": _sha256_bytes(_canonical_json(model_input_projection(case)))},
                score={"passed": False, "mismatches": {"runner_error": str(error)}},
            ))
    passed = sum(1 for row in case_runs if row.score.get("passed") is True)
    not_applicable = sum(1 for row in case_runs if row.score.get("qualification") == "N/A")
    failed = len(case_runs) - passed - not_applicable
    qualification_status = "PASS" if passed == len(case_runs) else "N/A" if failed == 0 else "FAIL"
    report = {
        "schema_version": RUN_SCHEMA_VERSION,
        "dataset": DATASET,
        "partition": partition,
        "started_at": started,
        "finished_at": _utc_now(),
        "fixture_sha256": loaded.fixture_sha256,
        "freeze_sha256": loaded.freeze_sha256,
        "fake_model_only": True,
        "real_provider_requests": 0,
        "qualification": {
            "status": qualification_status,
            "author_visible_gold": True,
            "external_blind_test": False,
            "real_model_generalization": False,
            "limits": [
                "FakeModelAdapter scripted controller; this is offline contract evidence only.",
                "Gold is author-visible and read only by score_case after execution.",
                "No real vault, network, Gemini call, or Provider Ledger write.",
            ],
        },
        "counts": {
            "total": len(case_runs),
            "passed": passed,
            "failed": failed,
            "not_applicable": not_applicable,
        },
        "cases": [row.to_dict() for row in case_runs],
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", choices=("dev", "final"), required=True)
    parser.add_argument("--output", required=True, help="Output directory or the literal 'unique'.")
    parser.add_argument("--final-acceptance", action="store_true", help="Permit the one-time frozen Final run.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_partition(
            repo_root=_REPO_ROOT,
            partition=args.partition,
            output=args.output,
            final_acceptance=args.final_acceptance,
        )
    except ContractV3RunnerError as error:
        print(f"V3 runner blocked: {error}", file=sys.stderr)
        return 2
    print(
        f"{report['partition']} {report['counts']['passed']}/{report['counts']['total']} "
        f"passed; qualification={report['qualification']['status']} "
        f"(FakeModelAdapter offline only)",
        flush=True,
    )
    return 0 if report["counts"]["failed"] == 0 else 1


__all__ = [
    "CaseRun", "ContractV3RunnerError", "FinalAcceptanceRequired",
    "FinalAlreadyClaimed", "LoadedPartition", "MaterializedSource",
    "RUN_SCHEMA_VERSION", "V3FixtureController", "load_partition",
    "materialize_case", "model_input_projection", "run_partition", "score_case",
]


if __name__ == "__main__":
    raise SystemExit(main())

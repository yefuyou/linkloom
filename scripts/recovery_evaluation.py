"""Run public-only canaries and score Agent Recovery reports after execution.

The default command is a post-run scorer: Gold is loaded only after a raw
report exists, and the scorer never starts Runtime or sends a Provider request.
The explicit ``--execute-public`` mode is a small offline canary adapter. It
accepts a development public fixture only, seeds Product, applies source
updates through the real repository boundary, and uses the existing bounded
controller with ``FakeModelAdapter``. Gold is not accepted by that mode.

The module imports the old reliability loader and row helper for schema
compatibility, then applies the recovery-specific citation observer contract
without changing the legacy runner. When flat observer fields are absent,
citation validity is derived from Product projection refs and completed
``read_verified_note`` records; no baseline receives a blanket N/A.

Example (Gold is loaded only for this post-run command)::

    $env:PYTHONPATH = 'src;.'
    $env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
    python scripts/recovery_evaluation.py `
      --public tests/fixtures/agent_recovery/public_frozen.json `
      --gold tests/fixtures/agent_recovery/gold_frozen.json `
      --report work/agent-recovery/raw_report.json `
      --output work/agent-recovery/recovery_report.json
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "integration"), str(ROOT / "src")]

# These are intentionally private imports from the established offline runner.
# The old runner remains unchanged; this module only reuses its JSON schema
# loaders and normal row comparison for a post-execution projection.
from run_real_agent_reliability import (  # noqa: E402
    _load_gold as _legacy_load_gold,
    _load_public as _legacy_load_public,
    _score_row as _legacy_score_row,
)


OBSERVER_FIELDS = ("citation_valid", "claim_supported", "verified_read_dependency")

# The code-freeze manifest records the evaluation/scoring boundary and the
# production files the root task has frozen for this first acceptance run.  It
# intentionally excludes itself and never rewrites the dataset freeze files.
CODE_FREEZE_FILES = (
    "scripts/recovery_evaluation.py",
    "scripts/reliability_probe.py",
    "src/linkloom/agents/retrieval_agent.py",
    "src/linkloom/context/assembler.py",
    "src/linkloom/retrieval_v2/runtime_backend.py",
    "src/linkloom/runtime/model_loop.py",
    "src/linkloom/runtime/progress.py",
    "src/linkloom/runtime/recovery_policy.py",
    "src/linkloom/ui/projection.py",
    "src/linkloom/ui/static/product.js",
    "tests/unit/test_recovery_evaluation.py",
    "tests/unit/test_graceful_partial_recovery.py",
    "tests/unit/test_recovery_passage_coverage.py",
)


def _normalise(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _selected_payload(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Accept the legacy report shape and a small direct-result shape.

    Legacy reports store ``selected`` plus ``baselines``.  The direct shape is
    useful for a model/provider report that has one selected result and stores
    it under ``result``.  Both become the old helper's shape before scoring.
    """
    selected = str(row.get("selected", "C"))
    baselines = row.get("baselines")
    if isinstance(baselines, dict) and isinstance(baselines.get(selected), dict):
        return selected, dict(baselines[selected])
    result = row.get("result")
    if isinstance(result, dict):
        return selected, dict(result)
    candidate = row.get(selected)
    if isinstance(candidate, dict):
        return selected, dict(candidate)
    # A bare baseline row is accepted for focused scorer tests and keeps the
    # report format easy to inspect by hand.
    return selected, dict(row)


def _observer_value(payload: dict[str, Any], field: str) -> bool | None:
    """Read an observer field from old flat rows or a nested observer block."""
    if field in payload:
        return bool(payload[field])
    observer = payload.get("citation_observer")
    if isinstance(observer, dict) and field in observer:
        return bool(observer[field])
    return None


def _iter_projection_sources(payload: dict[str, Any]):
    """Yield Product projection/ledger shapes from a raw case row."""
    for key in ("product_projection", "projection", "snapshot", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            yield value
    # A service result is sometimes the row itself.
    yield payload


def _product_observer_values(payload: dict[str, Any]) -> dict[str, bool | None]:
    """Observe citation-to-VerifiedRead association from Product artifacts.

    Product citations are opaque citation IDs projected from ``evidence[].ref``;
    source facts retain the underlying evidence refs.  A claim is supported
    only when every cited ref is present in a completed ``read_verified_note``
    result (or an evidence item explicitly marked verified).  Missing fields
    remain ``None`` so an observer gap cannot be silently reclassified as a
    successful claim.
    """
    observed_refs: set[str] = set()
    cited_refs: set[str] = set()
    claim_refs: list[set[str]] = []
    citation_map: dict[str, str] = {}
    saw_projection = False
    saw_claim = False

    for projection in _iter_projection_sources(payload):
        evidence_rows = projection.get("evidence")
        if isinstance(evidence_rows, list):
            saw_projection = True
            for evidence in evidence_rows:
                if not isinstance(evidence, dict):
                    continue
                ref = evidence.get("ref") or evidence.get("evidence_id")
                citation_id = evidence.get("id")
                if isinstance(ref, str) and ref:
                    if evidence.get("verified") is True or evidence.get("status") == "verified":
                        observed_refs.add(ref)
                    if isinstance(citation_id, str) and citation_id:
                        citation_map[citation_id] = ref

        tool_rows = (
            projection.get("tool_calls")
            or projection.get("calls")
            or projection.get("trace")
            or projection.get("ledger")
        )
        if isinstance(tool_rows, list):
            saw_projection = True
            for record in tool_rows:
                if not isinstance(record, dict):
                    continue
                tool = record.get("tool") or record.get("tool_id")
                if tool != "read_verified_note":
                    continue
                status = record.get("status")
                result = record.get("result")
                value = result.get("value") if isinstance(result, dict) else record.get("value")
                if not isinstance(value, dict):
                    continue
                ref = value.get("evidence_id") or value.get("ref")
                if isinstance(ref, str) and ref and status in {"completed", "verified"} and value.get("status") == "verified":
                    observed_refs.add(ref)

        source_investigation = projection.get("source_investigation")
        if isinstance(source_investigation, dict):
            saw_projection = True
            facts = source_investigation.get("facts")
            if isinstance(facts, list):
                for fact in facts:
                    if not isinstance(fact, dict):
                        continue
                    refs = fact.get("evidence_refs") or fact.get("citations")
                    if isinstance(refs, list):
                        mapped = {citation_map.get(ref, ref) for ref in refs if isinstance(ref, str)}
                        if mapped:
                            claim_refs.append(mapped)
                            cited_refs.update(mapped)
                            saw_claim = True

        def add_claim_refs(value: Any):
            nonlocal saw_claim
            if not isinstance(value, dict):
                return
            refs = value.get("evidence_refs")
            if isinstance(refs, list):
                mapped = {citation_map.get(ref, ref) for ref in refs if isinstance(ref, str)}
                if mapped:
                    claim_refs.append(mapped)
                    cited_refs.update(mapped)
                    saw_claim = True
            citations = value.get("citations")
            if isinstance(citations, list):
                mapped = {citation_map.get(ref, ref) for ref in citations if isinstance(ref, str)}
                if mapped:
                    claim_refs.append(mapped)
                    cited_refs.update(mapped)
                    saw_claim = True

        add_claim_refs(projection.get("decision"))
        for key in ("rationale", "conclusions", "actions", "uncertainty"):
            value = projection.get(key)
            if isinstance(value, list):
                for item in value:
                    add_claim_refs(item)
            else:
                add_claim_refs(value)

    if not saw_projection:
        return {field: None for field in OBSERVER_FIELDS}
    if not saw_claim:
        # A projection can expose only evidence while a refusal carries no
        # claim.  Leave claim validity unobserved; no-fact refusals do not need
        # invented citations.
        return {
            "citation_valid": None,
            "claim_supported": None,
            "verified_read_dependency": bool(observed_refs) if observed_refs else None,
        }
    citation_valid = bool(cited_refs) and cited_refs.issubset(observed_refs)
    claim_supported = bool(claim_refs) and all(refs and refs.issubset(observed_refs) for refs in claim_refs)
    return {
        "citation_valid": citation_valid,
        "claim_supported": claim_supported,
        "verified_read_dependency": bool(cited_refs) and bool(cited_refs.intersection(observed_refs)),
    }


def _observer_contract(selected: str, payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    derived = _product_observer_values(payload)
    for field in OBSERVER_FIELDS:
        value = _observer_value(payload, field)
        if value is None:
            value = derived[field]
        if value is None:
            values[field] = "not_observed"
        else:
            values[field] = value
    return {
        "selected_baseline": selected,
        "values": values,
        "product_citation_verified_read_association": True,
        "claim_support_is_not_overridden_by_baseline": True,
        "agent_claim_requires": list(OBSERVER_FIELDS),
        "contract_version": "agent-recovery-observer/v2",
    }


def _text_projection(payload: dict[str, Any]) -> str:
    """Project possible answer fields for forbidden-output checks."""
    values: list[str] = []
    for key in ("answer", "final_answer", "text", "facts", "claims", "reason"):
        value = payload.get(key)
        if value is not None:
            values.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return "\n".join(values)


def score_recovery_row(row: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Score one selected result with recovery and citation-observer rules.

    ``_legacy_score_row`` remains the baseline comparison. The returned
    ``pass`` value uses the corrected Product contract: every selected claim
    requires an actual citation-to-VerifiedRead association. Historical case
    labels A/B and runner baseline names A/B are not treated as a reason to
    mark claim support or verified reads N/A.
    """
    selected, payload = _selected_payload(row)
    canonical = {"selected": selected, "baselines": {selected: payload}}
    legacy = _legacy_score_row(canonical, expected)
    observer = _observer_contract(selected, payload)
    values = observer["values"]
    claimed = bool(payload.get("facts"))
    requires_verified = bool(expected.get("requires_verified_read", True))

    if not claimed:
        evidence_ok = True
    elif requires_verified:
        evidence_ok = all(values[field] is True for field in OBSERVER_FIELDS)
    else:
        evidence_ok = values["citation_valid"] is True and values["claim_supported"] is True

    conflict_sources = expected.get("conflict_sources", [])
    actual_sources = set(payload.get("sources", []) or [])
    conflict_sources_present = set(conflict_sources).issubset(actual_sources)
    forbidden_outputs = [str(item) for item in expected.get("forbidden_outputs", [])]
    output_text = _text_projection(payload).casefold()
    forbidden_output_absent = all(item.casefold() not in output_text for item in forbidden_outputs)

    expected_source_update = expected.get("source_update")
    stale_source_absent = True
    if isinstance(expected_source_update, dict):
        stale = expected_source_update.get("forbidden_stale_fact")
        stale_source_absent = not stale or _normalise(stale) not in _normalise(output_text)

    temporal_contract = expected.get("temporal_contract")
    future_fact_absent = True
    if isinstance(temporal_contract, dict):
        future = temporal_contract.get("forbidden_future_fact")
        future_fact_absent = not future or _normalise(future) not in _normalise(output_text)

    corrected_pass = bool(
        legacy["outcome_match"]
        and legacy["reason_match"]
        and legacy["facts_exact"]
        and legacy["required_sources_present"]
        and evidence_ok
        and conflict_sources_present
        and forbidden_output_absent
        and stale_source_absent
        and future_fact_absent
    )
    return {
        **legacy,
        "legacy_pass": bool(legacy["pass"]),
        "pass": corrected_pass,
        "evidence_ok": evidence_ok,
        "claimed_facts": claimed,
        "observer_contract": observer,
        "conflict_sources_present": conflict_sources_present,
        "forbidden_output_absent": forbidden_output_absent,
        "stale_source_absent": stale_source_absent,
        "future_fact_absent": future_fact_absent,
    }


# Public alias for callers that prefer the shorter name.
score_row = score_recovery_row
# Keep the established private helper spelling available to focused tests and
# small local reports while routing it through the corrected observer contract.
_score_row = score_recovery_row
load_public = _legacy_load_public
load_gold = _legacy_load_gold


def _freeze_path_for_public(public_path: Path) -> Path:
    """Return the adjacent identity freeze without reading Gold."""
    if public_path.name == "public_canary.json":
        candidate = public_path.with_name("freeze_canary.json")
    else:
        candidate = public_path.with_name("freeze.json")
    return candidate


def _assert_public_identity(public_path: Path, public_raw: bytes) -> Path | None:
    """Check a public file against its adjacent freeze, if one exists."""
    freeze_path = _freeze_path_for_public(public_path)
    if not freeze_path.exists():
        return None
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    expected = freeze.get("files", {}).get(public_path.name)
    actual = hashlib.sha256(public_raw).hexdigest()
    if expected and actual != expected:
        raise ValueError(f"frozen public identity mismatch: {public_path.name}")
    return freeze_path


def write_code_freeze_manifest(
    manifest_path: Path,
    *,
    public_path: Path,
    public_raw: bytes,
) -> dict[str, Any]:
    """Write an independent code SHA manifest for the frozen first run.

    This manifest is separate from ``tests/fixtures/agent_recovery/freeze.json``
    and ``freeze_canary.json``.  It records code and test inputs plus the public
    identity only; Gold is deliberately absent until post-run scoring.
    """
    files: dict[str, str] = {}
    for relative in CODE_FREEZE_FILES:
        path = ROOT / relative
        if not path.exists():
            raise FileNotFoundError(f"code-freeze file is missing: {relative}")
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    freeze_path = _freeze_path_for_public(public_path)
    freeze_hash = (
        hashlib.sha256(freeze_path.read_bytes()).hexdigest()
        if freeze_path.exists()
        else None
    )
    file_digest = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "agent-recovery-code-freeze/v1",
        "code_freeze_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "scope": "agent-recovery-optimization first frozen public execution",
        "gold_loaded": False,
        "gold_in_manifest": False,
        "files": files,
        "files_sha256": file_digest,
        "public": {
            "path": str(public_path),
            "sha256": hashlib.sha256(public_raw).hexdigest(),
            "partition": "frozen",
        },
        "dataset_freeze": {
            "path": str(freeze_path),
            "sha256": freeze_hash,
            "read_only_reference": True,
        },
        "manifest_excluded_from_files": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def load_recovery_datasets(
    public_path: Path,
    gold_path: Path,
    *,
    freeze_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Load and identity-check public/Gold after a report already exists."""
    public, public_raw = _legacy_load_public(public_path)
    ids = {str(case["id"]) for case in public["cases"]}
    gold, gold_raw = _legacy_load_gold(
        gold_path,
        expected_partition=str(public["partition"]),
        case_ids=ids,
    )
    freeze: dict[str, Any] | None = None
    if freeze_path is None:
        candidates = [public_path.with_name("freeze.json")]
        if public_path.name == "public_canary.json":
            candidates.insert(0, public_path.with_name("freeze_canary.json"))
        freeze_path = next((candidate for candidate in candidates if candidate.exists()), None)
    if freeze_path is not None and freeze_path.exists():
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        expected_files = freeze.get("files", {})
        for path, raw in ((public_path, public_raw), (gold_path, gold_raw)):
            expected_hash = expected_files.get(path.name)
            if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError(f"frozen identity mismatch: {path.name}")
    return public, gold, freeze


def score_report(report: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    """Add recovery scores to an already-created raw report."""
    expected_by_id = {str(item["id"]): item["expected"] for item in gold["cases"]}
    report_cases = report.get("cases")
    if not isinstance(report_cases, list):
        raise ValueError("report.cases must be a list")
    report_ids = {str(item.get("id")) for item in report_cases if isinstance(item, dict)}
    if report_ids != set(expected_by_id):
        raise ValueError("report case IDs must exactly match Gold case IDs")

    scored: list[dict[str, Any]] = []
    for row in report_cases:
        if not isinstance(row, dict):
            raise ValueError("each report case must be an object")
        result = score_recovery_row(row, expected_by_id[str(row["id"])])
        row["recovery_gold"] = result
        scored.append(result)
    report["recovery_score"] = {
        "rubric_version": gold.get("rubric_version"),
        "denominator": len(scored),
        "passed": sum(bool(item["pass"]) for item in scored),
        "outcome_accuracy": sum(bool(item["outcome_match"]) for item in scored),
        "exact_fact_accuracy": sum(bool(item["facts_exact"]) for item in scored),
        "required_source_coverage": sum(bool(item["required_sources_present"]) for item in scored),
        "evidence_ok_count": sum(bool(item["evidence_ok"]) for item in scored),
        "observer_contract": "Claims require actual Product citation-to-VerifiedRead association; historical case/baseline labels do not grant N/A.",
        "outcome_categories": _outcome_categories(report_cases),
    }
    return report


def _outcome_categories(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        selected, payload = _selected_payload(row)
        outcome = str(payload.get("outcome"))
        counts[outcome] = counts.get(outcome, 0) + 1
    return dict(sorted(counts.items()))


def _seed_product_case(public_case: dict[str, Any], run_root: Path):
    """Seed ProductRepository and apply declared source updates before query.

    This is intentionally a tiny adapter around the existing ProductApplication
    boundary.  A source update is written through ``handle_put`` and therefore
    creates a real repository version; the adapter never represents freshness
    by dropping a document or setting ``valid: false``.
    """
    from linkloom.ui.product import ProductApplication, ProductApplicationError

    def product_content(content: str, *, path: str) -> str:
        """Make legacy plain-text fixtures ingestible by Product.

        The frozen public corpus deliberately follows the old runner's plain
        document schema.  Product's source boundary requires timestamped
        speaker blocks, so the adapter adds deterministic synthetic headers at
        this boundary while retaining the fixture text in the receipt.  This
        keeps the canary offline and exercises the real repository/version
        update path without adding a fixture-specific ``valid: false`` escape.
        """
        probe_id = "probe_" + uuid4().hex
        artifact = app._artifact(workspace_id, probe_id, content)
        try:
            app._parse_artifact(artifact)
            return content
        except ProductApplicationError:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                raise ValueError(f"document content is empty: {path}")
            return "\n".join(
                f"[2026-10-15T00:{index:02d}:00Z] Recovery: {line}"
                for index, line in enumerate(lines)
            )

    run_root.mkdir(parents=True, exist_ok=True)
    app = ProductApplication(database_path=run_root / "product.sqlite", extractor=None)
    workspace = app.repository.create_workspace("Synthetic Recovery Canary")
    workspace_id = str(workspace["workspace_id"])
    app.repository.select_workspace(workspace_id)
    documents = public_case.get("documents")
    if not isinstance(documents, list) or not documents:
        app.close()
        raise ValueError(f"case {public_case.get('id')} requires documents")
    updates = public_case.get("source_updates", [])
    if not isinstance(updates, list):
        app.close()
        raise ValueError("source_updates must be a list")
    update_by_path: dict[str, dict[str, Any]] = {}
    for update in updates:
        if not isinstance(update, dict) or not isinstance(update.get("path"), str):
            app.close()
            raise ValueError("each source update needs a path")
        if update.get("applied_before_query") is not True:
            app.close()
            raise ValueError("source update must be applied_before_query")
        update_by_path[str(update["path"])] = update

    source_ids: dict[str, str] = {}
    initial_versions: dict[str, str] = {}
    for document in documents:
        if not isinstance(document, dict):
            app.close()
            raise ValueError("case documents must be objects")
        if document.get("valid") is False:
            app.close()
            raise ValueError("valid:false is forbidden in recovery execution fixtures")
        path = document.get("path")
        content = document.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            app.close()
            raise ValueError("document path/content must be text")
        source_id = "src_" + uuid4().hex
        update = update_by_path.get(path)
        initial_content = content
        if update:
            previous_fragment = str(update.get("previous_content"))
            current_fragment = str(update.get("current_content"))
            if current_fragment not in content:
                app.close()
                raise ValueError(f"source update current_content is not present in document: {path}")
            initial_content = content.replace(current_fragment, previous_fragment, 1)
        runtime_content = product_content(initial_content, path=path)
        artifact = app._artifact(workspace_id, source_id, runtime_content)
        spans = app._parse_artifact(artifact)
        source, _ = app.repository.create_source(
            workspace_id, source_id, path, artifact, spans
        )
        # The adapter has no semantic extractor/provider.  Mark the seeded
        # snapshot settled so Product's update boundary can accept the next
        # version; evidence remains the immutable spans above.
        app.repository.set_ingestion_result(
            workspace_id,
            source_id,
            str(source["source_version"]),
            status="COMPLETE",
            candidate_count=0,
            review_required_count=0,
        )
        source_ids[path] = source_id
        initial_versions[path] = str(source["source_version"])

    update_receipts: list[dict[str, Any]] = []
    for path, update in update_by_path.items():
        if path not in source_ids:
            app.close()
            raise ValueError(f"source update path is not in documents: {path}")
        document = next(item for item in documents if item.get("path") == path)
        document_content = document.get("content")
        current_fragment = update.get("current_content")
        if not isinstance(document_content, str) or not isinstance(current_fragment, str):
            app.close()
            raise ValueError(f"source update content must be text: {path}")
        if current_fragment not in document_content:
            app.close()
            raise ValueError(f"source update current_content is not present in document: {path}")
        runtime_content = product_content(document_content, path=path)
        response, status = app.handle_put(
            f"/api/sources/{source_ids[path]}",
            {"name": path, "content": runtime_content},
        )
        if status not in {200, 202}:
            app.close()
            raise RuntimeError(f"repository source update failed: {path} ({status})")
        current = app.repository.get_source(workspace_id, source_ids[path], include_content=True)
        if current is None or str(current["source_version"]) == initial_versions[path]:
            app.close()
            raise RuntimeError(f"repository source version did not advance: {path}")
        update_receipts.append({
            "path": path,
            "status": status,
            "source_id": source_ids[path],
            "old_source_version": initial_versions[path],
            "new_source_version": str(current["source_version"]),
            "fixture_current_content_sha256": hashlib.sha256(
                document_content.encode("utf-8")
            ).hexdigest(),
            "fixture_update_fragment_sha256": hashlib.sha256(
                current_fragment.encode("utf-8")
            ).hexdigest(),
            "product_current_content_sha256": str(current["content_sha256"]),
            "changed": bool(response.get("changed", True)),
        })
    return app, workspace_id, update_receipts


def _classify_product_terminal(snapshot: dict[str, Any]) -> tuple[str, str | None, str]:
    """Map Product terminal projection to legacy outcome plus explicit status.

    ``runtime_failure`` remains the legacy outcome for a budget stop so frozen
    Gold stays comparable, while ``termination_status`` exposes the distinct
    budget class. Provider outcome uncertainty is a separate outcome, matching
    the established reliability runner contract.
    """
    kind = str(snapshot.get("kind") or "")
    if kind == "success":
        return "complete", None, "complete"
    if kind == "insufficient":
        status = (snapshot.get("uncertainty") or {}).get("status")
        investigation = snapshot.get("source_investigation") or {}
        has_facts = bool(investigation.get("facts"))
        return (
            "partial" if has_facts else "refusal",
            "conflicting_evidence" if status == "conflicting_evidence" else "evidence_insufficient",
            "partial" if has_facts else "insufficient_evidence",
        )

    error = snapshot.get("error") or {}
    classification = str(error.get("classification") or "").casefold()
    code = str(error.get("code") or "").casefold()
    if classification in {"budget", "budget_exhausted"} or "budget" in code:
        return "runtime_failure", "budget_exhausted", "budget_exhausted"
    if classification in {
        "provider_outcome_uncertain", "provider_uncertain", "provider_unknown",
    } or "unknown_provider" in code or "provider_uncertain" in code:
        return "provider_uncertain", code or "provider_outcome_uncertain", "provider_outcome_uncertain"
    return "runtime_failure", code or classification or "runtime_error", classification or "runtime_failure"


def run_offline_product_case(public_case: dict[str, Any], run_root: Path) -> dict[str, Any]:
    """Run one public case through ProductInvestigationService with FakeModel.

    Gold is not accepted by this function.  The returned row is compatible with
    the post-run scorer and retains the actual Product projection plus the
    durable tool ledger needed to observe citation-to-VerifiedRead linkage.
    """
    from run_bounded_multihop import SourceOnlyController
    from linkloom.agents.model_adapter import FakeModelAdapter
    from linkloom.runtime.team_decision_planner import plan_relation_investigation
    from linkloom.ui.investigation import ProductInvestigationService

    app, workspace_id, update_receipts = _seed_product_case(public_case, run_root)
    query = str(public_case["query"])
    plan = plan_relation_investigation(query)
    started = time.perf_counter()
    try:
        if plan is None:
            return {
                "id": public_case["id"],
                "selected": "C",
                "baselines": {"C": {
                    "outcome": "unsupported", "reason": "planning_failed", "facts": [], "sources": [],
                    "citation_valid": False, "claim_supported": False,
                    "verified_read_dependency": False,
                }},
                "source_update_receipts": update_receipts,
                "real_provider_requests": 0,
                "termination_status": "planning_failed",
            }

        controller = SourceOnlyController()
        model = FakeModelAdapter([controller] * 20)
        service = ProductInvestigationService(
            app,
            model_factory=lambda _query: model,
            run_mode="OFFLINE_FAKE",
            external_provider=False,
            relation_max_steps=12,
        )
        initial = service.start(query, workspace_id, task_plan=plan)
        run_id = initial["run"]["id"]
        snapshot = initial
        deadline = time.monotonic() + 60
        while snapshot.get("kind") == "running":
            if time.monotonic() >= deadline:
                raise TimeoutError("offline recovery case exceeded 60 seconds")
            time.sleep(0.02)
            snapshot = service.inspect(run_id) or snapshot
        job = service._jobs[run_id]
        state = job.backend.engine.checkpointer.get_latest(snapshot["run"]["thread_id"])
        tool_calls = [record.to_dict() for record in (state.tool_ledger if state else [])]
        projection_payload = {**snapshot, "tool_calls": tool_calls}
        observer = _product_observer_values({"product_projection": projection_payload})
        investigation = snapshot.get("source_investigation") or {}
        facts = [str(item["value"]).casefold() for item in investigation.get("facts", [])
                 if isinstance(item, dict) and isinstance(item.get("value"), str)]
        sources = list(dict.fromkeys(
            str(item.get("source_name") or item.get("relative_path"))
            for item in snapshot.get("evidence", []) if isinstance(item, dict)
            and (item.get("source_name") or item.get("relative_path"))
        ))
        outcome, reason, termination_status = _classify_product_terminal(snapshot)
        error = snapshot.get("error") or {}
        failure_classification = str(error.get("classification") or "") or None
        baseline = {
            "outcome": outcome,
            "reason": reason,
            "facts": facts,
            "sources": sources,
            "citation_valid": observer["citation_valid"] is True,
            "claim_supported": observer["claim_supported"] is True,
            "verified_read_dependency": observer["verified_read_dependency"] is True,
            "citation_observer": observer,
            "product_projection": projection_payload,
            "tool_calls": tool_calls,
            "timing_ms": round((time.perf_counter() - started) * 1000, 3),
            "provider_uncertainty": termination_status == "provider_outcome_uncertain",
            "termination_status": termination_status,
            "failure_classification": failure_classification,
        }
        return {
            "id": public_case["id"],
            "selected": "C",
            "baselines": {"C": baseline},
            "source_update_receipts": update_receipts,
            "real_provider_requests": 0,
            "termination_status": termination_status,
        }
    finally:
        app.close()


def execute_offline_public(
    public_path: Path,
    *,
    output: Path,
    case_ids: list[str] | None = None,
    allow_frozen: bool = False,
    code_freeze_manifest: Path | None = None,
) -> dict[str, Any]:
    """Execute public cases with no Gold/model input.

    Development canaries are the default. Frozen acceptance execution requires
    the explicit ``allow_frozen`` acknowledgement and writes an independent
    code-freeze manifest before the first case starts.
    """
    if output.exists():
        raise FileExistsError(output)
    public, public_raw = _legacy_load_public(public_path)
    partition = str(public.get("partition") or "")
    role = str(public.get("dataset_role") or "")
    is_frozen = partition == "frozen" or role == "acceptance"
    if is_frozen:
        if not allow_frozen:
            raise ValueError("frozen execution requires explicit --allow-frozen")
        if public_path.name != "public_frozen.json" or role != "acceptance":
            raise ValueError("--allow-frozen is restricted to public_frozen.json")
        freeze_path = _assert_public_identity(public_path, public_raw)
        if freeze_path is None:
            raise ValueError("frozen execution requires adjacent freeze.json")
        if code_freeze_manifest is None:
            code_freeze_manifest = output.parent / "agent-recovery-code-freeze-manifest.json"
        code_manifest = write_code_freeze_manifest(
            code_freeze_manifest, public_path=public_path, public_raw=public_raw
        )
    else:
        if role != "development-canary" or public.get("stage") != "development":
            raise ValueError("--execute-public accepts development canary datasets only")
        if public.get("canary") is not True:
            raise ValueError("--execute-public requires canary=true")
        _assert_public_identity(public_path, public_raw)
        code_manifest = None
    cases = list(public["cases"])
    if case_ids is not None:
        selected = set(case_ids)
        ids = {str(case["id"]) for case in cases}
        if not selected <= ids:
            raise ValueError("unknown public case id")
        cases = [case for case in cases if case["id"] in selected]
    budget = public.get("canary_budget") or {}
    max_cases = int(budget.get("max_cases", len(cases)))
    if len(cases) > max_cases:
        raise ValueError("canary case count exceeds declared budget")
    max_requests = int(budget.get("max_provider_requests", 20))
    if max_requests > 20:
        raise ValueError("canary provider request cap exceeds 20")
    run_root = output.parent / (output.stem + "-runs")
    rows = [run_offline_product_case(case, run_root / str(case["id"])) for case in cases]
    report = {
        "schema_version": "agent-recovery-offline-report/v1",
        "dataset": {"path": str(public_path), "partition": public["partition"],
                    "sha256": hashlib.sha256(public_raw).hexdigest(), "case_count": len(rows)},
        "execution_mode": "offline_product_public_only",
        "allow_frozen": allow_frozen,
        "gold_loaded_after_execution": False,
        "gold_not_runtime_input": True,
        "authoring_disclosure": public.get("authoring_disclosure"),
        "fake_model_only": True,
        "real_provider_requests": 0,
        "canary_budget": {"max_provider_requests": max_requests, "max_cases": max_cases,
                          "concurrency": int(budget.get("concurrency", 1))},
        "termination_status_counts": _termination_status_counts(rows),
        "cases": rows,
    }
    if code_manifest is not None:
        report["code_freeze_manifest"] = str(code_freeze_manifest)
        report["code_freeze_manifest_sha256"] = hashlib.sha256(
            code_freeze_manifest.read_bytes()
        ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _termination_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("termination_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--report", type=Path, help="post-execution raw report")
    parser.add_argument("--execute-public", type=Path, help="execute public development cases offline")
    parser.add_argument("--case-ids", help="comma-separated public IDs for --execute-public")
    parser.add_argument(
        "--allow-frozen",
        action="store_true",
        help="explicitly acknowledge first execution of public_frozen.json",
    )
    parser.add_argument(
        "--code-freeze-manifest",
        type=Path,
        help="independent code SHA manifest for --allow-frozen execution",
    )
    parser.add_argument("--output", type=Path, default=Path("work/agent-recovery/recovery_report.json"))
    parser.add_argument("--freeze", type=Path, help="adjacent freeze.json by default")
    args = parser.parse_args(argv)

    if args.execute_public is not None:
        if args.gold is not None or args.report is not None:
            parser.error("--execute-public cannot be combined with --gold or --report")
        if args.code_freeze_manifest is not None and not args.allow_frozen:
            parser.error("--code-freeze-manifest requires --allow-frozen")
        case_ids = [item for item in (args.case_ids or "").split(",") if item] or None
        result = execute_offline_public(
            args.execute_public,
            output=args.output,
            case_ids=case_ids,
            allow_frozen=args.allow_frozen,
            code_freeze_manifest=args.code_freeze_manifest,
        )
        print(json.dumps({
            "state": "COMPLETE",
            "cases": len(result["cases"]),
            "real_provider_requests": result["real_provider_requests"],
            "termination_status_counts": result["termination_status_counts"],
            "output": str(args.output),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.public is None or args.gold is None or args.report is None:
        parser.error("--public, --gold, and --report are required for post-run scoring")

    # The raw report must exist before Gold is loaded.  This command is a
    # scoring projection and is never the Runtime/model execution entrypoint.
    report = json.loads(args.report.read_text(encoding="utf-8"))
    _public, gold, freeze = load_recovery_datasets(args.public, args.gold, freeze_path=args.freeze)
    result = score_report(report, gold)
    result["gold_loaded_after_execution"] = True
    result["gold_not_runtime_input"] = True
    result["authoring_disclosure"] = _public.get("authoring_disclosure")
    result["dataset"] = {
        "public": str(args.public),
        "gold": str(args.gold),
        "partition": _public.get("partition"),
        "case_count": len(_public.get("cases", [])),
        "public_sha256": hashlib.sha256(args.public.read_bytes()).hexdigest(),
        "gold_sha256": hashlib.sha256(args.gold.read_bytes()).hexdigest(),
    }
    if freeze is not None:
        result["freeze_schema_version"] = freeze.get("schema_version")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["recovery_score"], ensure_ascii=False, sort_keys=True))
    return 0 if result["recovery_score"]["passed"] == result["recovery_score"]["denominator"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

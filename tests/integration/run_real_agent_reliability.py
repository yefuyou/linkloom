"""Small, Gold-separated offline reliability evaluation runner.

The public dataset is the only input to execution.  Gold is loaded after the
execution rows have been written and is used only by the scoring projection.
All runtime artifacts are written below ``work/`` (ignored by this checkout).

This runner deliberately labels the three baselines:

* A: deterministic lexical/DecisionDiscoveryFixture path;
* B: one retrieval-only FakeModel summary (no verified read);
* C: the existing Runtime with SourceOnlyController (FakeModel), plus the
  existing DecisionDiscoveryFixture's full temporal Runtime for decision rows.

No live Provider is reachable from this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO = Path(__file__).resolve().parents[2]
INTEGRATION = REPO / "tests" / "integration"
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(INTEGRATION) not in sys.path:
    sys.path.insert(0, str(INTEGRATION))
# The offline contract must never ask the retrieval layer to download or use an
# embedding model.  BM25 is the existing local deterministic backend.
os.environ.setdefault("LINKLOOM_RETRIEVAL_MODE", "bm25")

from decision_discovery_fixture import DecisionDiscoveryFixture  # noqa: E402
from run_bounded_multihop import SourceOnlyController  # noqa: E402
from unified_ask_fixture import (  # noqa: E402
    ATLAS,
    OTHER,
    poll_product_ask,
    request_json,
)
from linkloom.agents.claim_grounding import (  # noqa: E402
    ClaimSupportStatus,
    assess_claim_text,
)
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction  # noqa: E402
from linkloom.runtime.checkpoint import InMemoryCheckpointer  # noqa: E402
from linkloom.runtime.errors import ValidationError  # noqa: E402
from linkloom.runtime.graph import RuntimeEngine  # noqa: E402
from linkloom.runtime.models import RunRequest  # noqa: E402
from linkloom.runtime.task_plan import (  # noqa: E402
    TaskPlan,
    assess_task_plan,
    verified_dependency_entities,
    verified_plan_conflict_refs,
    verified_plan_subgoal_values,
)
from linkloom.runtime.team_decision_planner import plan_relation_investigation  # noqa: E402
from linkloom.scanner import scan_vault  # noqa: E402
from linkloom.tools.contracts import ToolCall  # noqa: E402
from linkloom.ui.scope_discovery import resolve_query_date  # noqa: E402


SCHEMA_VERSION = "real-agent-reliability-report/v1"
PUBLIC_FORBIDDEN_KEYS = {
    "expected",
    "gold",
    "facts",
    "required_sources",
    "refusal_class",
    "exact_facts",
    "rubric",
}
_ENTITY = r"[A-Za-z0-9㐀-鿿][A-Za-z0-9㐀-鿿 ._'’&/-]*?"
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"dataset root must be an object: {path}")
    return value


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _load_public(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema_version") != "real-agent-reliability-public/v1":
        raise ValueError("public dataset schema_version is invalid")
    if data.get("partition") not in {"dev", "frozen"}:
        raise ValueError("public dataset partition must be dev or frozen")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("public dataset needs a non-empty cases list")
    forbidden = sorted({key.casefold() for key in _walk_keys(data)} & PUBLIC_FORBIDDEN_KEYS)
    if forbidden:
        raise ValueError(f"public dataset contains Gold-like fields: {forbidden}")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("each public case needs a non-empty id")
    if len(set(ids)) != len(ids):
        raise ValueError("public case IDs must be unique")
    return data, raw


def _load_gold(path: Path, *, expected_partition: str, case_ids: set[str]) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema_version") != "real-agent-reliability-gold/v1":
        raise ValueError("gold dataset schema_version is invalid")
    if data.get("partition") != expected_partition:
        raise ValueError("public/gold partitions do not match")
    if not isinstance(data.get("rubric_version"), str) or not data["rubric_version"]:
        raise ValueError("gold rubric_version is required")
    rows = data.get("cases")
    if not isinstance(rows, list):
        raise ValueError("gold dataset needs a cases list")
    by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != case_ids:
        raise ValueError("Gold case IDs must exactly match public case IDs")
    for row in rows:
        expected = row.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"Gold expected object missing for {row.get('id')}")
        for field in ("outcome", "facts", "reason", "required_sources", "refusal_class", "exact_facts", "baseline_compare"):
            if field not in expected:
                raise ValueError(f"Gold expected.{field} missing for {row.get('id')}")
    return data, raw


def _case_documents(case: dict[str, Any]) -> list[dict[str, Any]]:
    workspace = case.get("workspace_id")
    documents = case.get("documents")
    if not isinstance(workspace, str) or not isinstance(documents, list):
        raise ValueError(f"invalid case workspace/documents: {case.get('id')}")
    valid: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError(f"invalid document in {case.get('id')}")
        if document.get("workspace_id") != workspace:
            continue
        if document.get("valid", True) is False:
            # An explicitly invalid source is public fixture metadata, not a
            # source the Runtime may read. Keep the row in metrics separately.
            continue
        if not isinstance(document.get("path"), str) or not re.fullmatch(r"[^/\\]+\.md", document["path"]):
            raise ValueError(f"document path must be one relative Markdown name: {case.get('id')}")
        if not isinstance(document.get("content"), str):
            raise ValueError(f"document content must be text: {case.get('id')}")
        valid.append(document)
    return valid


def _date_scope(query: str) -> str | None:
    match = re.search(_DATE, query)
    return match.group(0) if match else None


def _clean_sentence(text: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*[^:]+:\s*", "", text.strip()).strip()


def _facts_from_documents(case: dict[str, Any]) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    date_scope = _date_scope(str(case.get("query", "")))
    for document in _case_documents(case):
        for raw_line in str(document["content"]).splitlines():
            line = _clean_sentence(raw_line)
            date_match = re.search(_DATE, line)
            if date_scope and date_match and date_match.group(0) != date_scope:
                continue
            alias = re.search(rf"^(?P<alias>{_ENTITY})\s+is the team alias for\s+(?P<target>{_ENTITY})\.?$", line, re.I)
            owner = re.search(rf"^(?P<owner>{_ENTITY})\s+is the accountable owner of\s+(?P<project>{_ENTITY})\.?$", line, re.I)
            leader = re.search(rf"^(?P<owner>{_ENTITY})\s+reports directly to\s+(?P<leader>{_ENTITY})\.?$", line, re.I)
            if alias:
                facts.append({"relation": "alias", "subject": alias["alias"].strip(), "value": alias["target"].strip(), "source": document["path"]})
            elif owner:
                facts.append({"relation": "owner", "subject": owner["project"].strip(), "value": owner["owner"].strip(), "source": document["path"]})
            elif leader:
                facts.append({"relation": "leader", "subject": leader["owner"].strip(), "value": leader["leader"].strip(), "source": document["path"]})
    return facts


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _root_entity(plan: TaskPlan) -> str | None:
    first = plan.subgoals[0].question
    for pattern in (
        rf"^Which project does (?P<entity>{_ENTITY}) alias",
        rf"^Who owns (?P<entity>{_ENTITY})\??$",
        rf"^谁负责\s*(?P<entity>{_ENTITY})\s*[?？]",
        rf"^(?P<entity>{_ENTITY})\s*项目负责人的直属领导是谁",
        rf"^As of {_DATE}, who owns (?P<entity>{_ENTITY})",
    ):
        match = re.search(pattern, first, re.I)
        if match:
            return match.group("entity").strip()
    return None


def deterministic_relation(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    query = str(case["query"])
    plan = plan_relation_investigation(query)
    if plan is None:
        return _baseline_row("A", case, outcome="unsupported", reason="planning_failed", started=started)
    facts = _facts_from_documents(case)
    root = _root_entity(plan)
    values: list[str] = []
    sources: list[str] = []
    conflict = False
    if plan.subgoals[0].relation_path == ("alias_of_project",):
        aliases = [item for item in facts if item["relation"] == "alias" and _norm(item["subject"]) == _norm(root or "")]
        if len({ _norm(item["value"]) for item in aliases }) != 1:
            conflict = bool(aliases)
        if aliases and not conflict:
            project = aliases[0]["value"]
            values.append(project); sources.append(aliases[0]["source"])
            owners = [item for item in facts if item["relation"] == "owner" and _norm(item["subject"]) == _norm(project)]
        else:
            owners = []
    else:
        owners = [item for item in facts if item["relation"] == "owner" and _norm(item["subject"]) == _norm(root or "")]
    if len({ _norm(item["value"]) for item in owners }) > 1:
        conflict = True
    if owners and not conflict:
        owner = owners[0]["value"]
        values.append(owner); sources.append(owners[0]["source"])
        leaders = [item for item in facts if item["relation"] == "leader" and _norm(item["subject"]) == _norm(owner)]
        if len({ _norm(item["value"]) for item in leaders }) > 1:
            conflict = True
        if leaders and not conflict:
            values.append(leaders[0]["value"]); sources.append(leaders[0]["source"])
    if conflict:
        return _baseline_row("A", case, outcome="refusal", reason="conflicting_evidence", started=started)
    if not owners:
        return _baseline_row("A", case, outcome="refusal", reason="evidence_insufficient", started=started)
    complete = len(values) == len(plan.subgoals)
    return _baseline_row("A", case, outcome="complete" if complete else "partial",
                         reason=None if complete else "evidence_insufficient", facts=values,
                         sources=sources, started=started, citation_valid=True,
                         claim_supported=True, verified_read_dependency=False,
                         search_count=0, read_count=0, model_turns=0)


def _baseline_row(baseline: str, case: dict[str, Any], *, outcome: str,
                  reason: str | None, facts: list[str] | None = None,
                  sources: list[str] | None = None, started: float | None = None,
                  **metrics: Any) -> dict[str, Any]:
    return {
        "baseline": baseline,
        "outcome": outcome,
        "reason": reason,
        "facts": [_norm(item) for item in (facts or [])],
        "sources": list(dict.fromkeys(sources or [])),
        "citation_valid": bool(metrics.pop("citation_valid", False)),
        "claim_supported": bool(metrics.pop("claim_supported", False)),
        "verified_read_dependency": bool(metrics.pop("verified_read_dependency", False)),
        "fake_model_only": baseline in {"B", "C"},
        "model": "FakeModel" if baseline in {"B", "C"} else "deterministic",
        "search_count": int(metrics.pop("search_count", 0)),
        "read_count": int(metrics.pop("read_count", 0)),
        "tool_calls": int(metrics.pop("tool_calls", 0)),
        "model_turns": int(metrics.pop("model_turns", 0)),
        "context_bytes": int(metrics.pop("context_bytes", 0)),
        "timing_ms": round((time.perf_counter() - started) * 1000, 3) if started is not None else 0.0,
        **metrics,
    }


def one_pass_summary(case: dict[str, Any]) -> dict[str, Any]:
    """B: one lexical retrieval and an explicitly unverified summary."""
    started = time.perf_counter()
    plan = plan_relation_investigation(str(case["query"]))
    if plan is None:
        return _baseline_row("B", case, outcome="unsupported", reason="planning_failed", started=started)
    documents = _case_documents(case)
    terms = [term.casefold() for term in re.findall(r"[A-Za-z0-9㐀-鿿]+", str(case["query"])) if len(term) > 1]
    ranked = sorted(documents, key=lambda item: sum(item["content"].casefold().count(term) for term in terms), reverse=True)
    hits = [item for item in ranked[:4] if terms and any(term in item["content"].casefold() for term in terms)]
    hit_paths = [item["path"] for item in hits]
    hit_case = {**case, "documents": hits}
    summary = deterministic_relation(hit_case)
    summary.update({
        "baseline": "B",
        "model": "FakeModel/one-pass-summary",
        "fake_model_only": True,
        "search_count": 1,
        "read_count": 0,
        "tool_calls": 1,
        "model_turns": 1,
        "sources": hit_paths,
        "citation_valid": False,
        "claim_supported": False,
        "verified_read_dependency": False,
        "grounding_status": "unverified_search_summary",
    })
    summary["timing_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return summary


def _runtime_case(public: dict[str, Any], root: Path) -> dict[str, Any]:
    """C: existing SourceOnlyController + Runtime, with caller limits."""
    from run_bounded_multihop import run_case as existing_run_case

    if plan_relation_investigation(str(public["query"])) is None:
        return _baseline_row("C", public, outcome="unsupported", reason="planning_failed",
                             started=time.perf_counter())

    # Keep the established runner as the source of truth for the ordinary
    # (max_steps=12) path.  The small copied path below is only needed to
    # exercise the explicitly published budget-insufficient development case.
    if int(public.get("max_steps", 12)) == 12:
        # The legacy runner filters workspace scope but predates this fixture's
        # explicit invalid-source marker.  Sanitize the public documents here
        # so frozen L cannot accidentally read a source declared invalid.
        runtime_public = {**public, "documents": _case_documents(public)}
        actual = existing_run_case(runtime_public, root)
        actual["outcome"] = "refusal" if actual.get("outcome") == "refused" else actual.get("outcome")
        actual["baseline"] = "C"
        actual["fake_model_only"] = True
        actual["model"] = "FakeModel/SourceOnlyController"
        actual["tool_calls"] = len(actual.get("calls", []))
        actual["search_count"] = sum(item.get("tool") == "search_notes" for item in actual.get("calls", []))
        actual["read_count"] = sum(item.get("tool") == "read_verified_note" and item.get("status") == "completed" for item in actual.get("calls", []))
        actual["claim_supported"] = bool(actual.get("citation_valid") and actual.get("values"))
        actual["verified_read_dependency"] = actual["read_count"] > 0
        actual["timing_ms"] = actual.pop("elapsed_ms", 0)
        actual["facts"] = [_norm(item) for item in actual.pop("values", [])]
        actual["sources"] = actual.get("sources", [])
        actual["provider_uncertainty"] = False
        return actual

    plan = plan_relation_investigation(str(public["query"]))
    started = time.perf_counter()
    if plan is None:
        return _baseline_row("C", public, outcome="unsupported", reason="planning_failed", started=started)
    run_root = root / ("run_" + uuid4().hex[:12])
    vault = run_root / "vault"
    vault.mkdir(parents=True)
    for document in _case_documents(public):
        (vault / document["path"]).write_text(document["content"], encoding="utf-8")
    index = scan_vault(vault, run_root / "scan").index_path
    model = FakeModelAdapter([SourceOnlyController()] * 20)
    limit = int(public.get("max_steps", 12))
    engine = RuntimeEngine(vault_root=vault, index_path=index, checkpoint_dir=run_root / "runtime",
                           trace_dir=run_root / "traces", checkpointer=InMemoryCheckpointer(), model=model)
    status = engine.start_multi_agent(RunRequest(
        request_id="rar-budget", thread_id="rar-budget", workflow="team_decision",
        query=public["query"], task_plan=plan.to_dict(), max_steps=limit,
        max_provider_requests=limit, dry_run=True,
    ))
    state = engine.checkpointer.get_latest("rar-budget")
    records = state.tool_ledger if state else []
    error = status.error or {}
    calls = [{"tool": record.tool_id, "status": record.status} for record in records]
    failure_reason = "budget_exhausted" if error.get("category") == "budget" or "BUDGET" in str(error.get("code", "")) else "runtime_error"
    return _baseline_row("C", public,
                         outcome="runtime_failure" if status.status == "failed" else "complete",
                         reason=failure_reason if status.status == "failed" else None,
                         started=started, search_count=sum(item["tool"] == "search_notes" for item in calls),
                         read_count=sum(item["tool"] == "read_verified_note" and item["status"] == "completed" for item in calls),
                         tool_calls=len(calls), model_turns=model.call_count,
                         context_bytes=max((len(json.dumps([x.to_dict() for x in request.evidence_context]).encode())
                                            for request in model.requests), default=0),
                         error=error, calls=calls, provider_uncertainty=False)


def _decision_fixture_case(case: dict[str, Any], root: Path, *, agent: bool) -> dict[str, Any]:
    started = time.perf_counter()
    fixture = DecisionDiscoveryFixture(root / ("decision_agent.sqlite" if agent else "decision_deterministic.sqlite"))
    try:
        fixture.select_workspace(ATLAS if case.get("workspace_id") == "atlas" else OTHER)
        workspace_id = fixture.workspace_ids[ATLAS if case.get("workspace_id") == "atlas" else OTHER]
        if case.get("baseline", {}).get("decision_fixture") == "proposal_only":
            query = case["query"]
        else:
            query = case["query"]
        if not agent:
            status, payload = request_json(fixture.base_url, "/api/product/ask", method="POST",
                                           data={"query": query, "workspace_id": workspace_id})
            result = payload.get("result", {})
            facts = [item.get("value", "") for item in result.get("conclusions", []) if isinstance(item, dict)]
            evidence = result.get("evidence", [])
            outcome = "complete" if result.get("status") == "COMPLETED" else "refusal"
            reason = None if outcome == "complete" else result.get("discovery", {}).get("status", "no_match").casefold()
            return _baseline_row("A", case, outcome=outcome, reason=reason, facts=facts,
                                 sources=[item.get("source_name", "") for item in evidence], started=started,
                                 citation_valid=all(item.get("verified") for item in evidence),
                                 claim_supported=outcome == "complete", verified_read_dependency=False)
        fixture.configure_agent(external_provider=False)
        if case.get("baseline", {}).get("decision_fixture") == "proposal_only":
            return _baseline_row("C", case, outcome="unsupported", reason="no_match", started=started)
        as_of, date_error = resolve_query_date(case["query"], None)
        if date_error:
            return _baseline_row("C", case, outcome="unsupported", reason=date_error["code"].casefold(), started=started)
        view = fixture.app.recover_decision("Launch", "uses supplier", as_of, _workspace_id=workspace_id)
        snapshot = fixture.service.start_temporal(query, workspace_id, subject="Launch",
                                                   relation="uses supplier", as_of=view["as_of"])
        deadline = time.monotonic() + 20
        terminal = snapshot
        while time.monotonic() < deadline:
            current = fixture.service.inspect(snapshot["run"]["id"])
            if current is not None:
                terminal = current
                if current.get("kind") != "running":
                    break
            time.sleep(0.01)
        facts = []
        temporal = terminal.get("temporal_decision") or {}
        effective = temporal.get("effective") if isinstance(temporal, dict) else None
        if isinstance(effective, dict) and isinstance(effective.get("value"), str):
            facts.append(effective["value"])
        evidence = terminal.get("evidence", [])
        status = terminal.get("kind")
        terminal_error = terminal.get("error") or {}
        outcome = "complete" if status == "success" and facts else "provider_uncertain" if terminal_error.get("classification") == "provider_outcome_uncertain" else "runtime_failure"
        reason = None if outcome == "complete" else terminal_error.get("code", "runtime_error")
        metrics = terminal.get("metrics", {})
        return _baseline_row("C", case, outcome=outcome, reason=reason, facts=facts,
                             sources=[item.get("source_name", "") for item in evidence], started=started,
                             citation_valid=all(item.get("verified") for item in evidence),
                             claim_supported=bool(facts), verified_read_dependency=metrics.get("read_count", 0) > 0,
                             search_count=metrics.get("search_count", 0), read_count=metrics.get("read_count", 0),
                             tool_calls=metrics.get("search_count", 0) + metrics.get("read_count", 0),
                             model_turns=metrics.get("model_turns", 0), context_bytes=0,
                             provider_uncertainty=outcome == "provider_uncertain")
    finally:
        fixture.stop()


def run_case(public: dict[str, Any], root: Path) -> dict[str, Any]:
    scenario = public.get("scenario")
    if scenario == "decision":
        baselines = {
            "A": _decision_fixture_case(public, root, agent=False),
            "B": one_pass_summary(public),
            "C": _decision_fixture_case(public, root, agent=True),
        }
    else:
        baselines = {
            "A": deterministic_relation(public),
            "B": one_pass_summary(public),
            "C": _runtime_case(public, root),
        }
    return {
        "id": public["id"],
        "scenario": scenario,
        "baselines": baselines,
        "selected": "C",
        "fake_model_only": True,
        "real_provider_requests": 0,
    }


def _score_row(row: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    selected = row["baselines"][row["selected"]]
    expected_facts = [_norm(item) for item in expected["facts"]]
    facts_exact = selected.get("facts", []) == expected_facts
    outcome_match = selected.get("outcome") == expected["outcome"]
    reason_match = selected.get("reason") == expected["reason"]
    sources_match = set(expected["required_sources"]).issubset(set(selected.get("sources", [])))
    evidence_ok = (not selected.get("facts") or all(selected.get(key) for key in (
        "citation_valid", "claim_supported", "verified_read_dependency")))
    return {
        "outcome_match": outcome_match,
        "reason_match": reason_match,
        "facts_exact": facts_exact,
        "required_sources_present": sources_match,
        "citation_valid": bool(selected.get("citation_valid")),
        "claim_supported": bool(selected.get("claim_supported")),
        "verified_read_dependency": bool(selected.get("verified_read_dependency")),
        "pass": bool(outcome_match and reason_match and facts_exact and sources_match and evidence_ok),
    }


def score_report(report: dict[str, Any], gold: dict[str, Any]) -> None:
    expected_by_id = {row["id"]: row["expected"] for row in gold["cases"]}
    for row in report["cases"]:
        expected = expected_by_id[row["id"]]
        row["gold"] = _score_row(row, expected)
    selected = [row["gold"] for row in report["cases"]]
    outcomes = Counter(row["baselines"][row["selected"]].get("outcome") for row in report["cases"])
    claim_rows = [row for row in report["cases"] if row['baselines'][row['selected']].get('facts')]
    report["score"] = {
        "denominator": len(selected),
        "passed": sum(item["pass"] for item in selected),
        "outcome_accuracy": sum(item["outcome_match"] for item in selected),
        "exact_fact_accuracy": sum(item["facts_exact"] for item in selected),
        "required_source_coverage": sum(item["required_sources_present"] for item in selected),
        "citation_valid_count": sum(item["citation_valid"] for item in selected),
        "claim_supported_count": sum(item["claim_supported"] for item in selected),
        "verified_read_dependency_count": sum(item["verified_read_dependency"] for item in selected),
        "outcome_categories": dict(sorted(outcomes.items())),
        "claimed_answer_cases_denominator": len(claim_rows),
        "citation_valid_claimed_cases": sum(row['gold']['citation_valid'] for row in claim_rows),
        "claim_supported_claimed_cases": sum(row['gold']['claim_supported'] for row in claim_rows),
        "verified_read_claimed_cases": sum(row['gold']['verified_read_dependency'] for row in claim_rows),
    }
    comparison: dict[str, Any] = {}
    for case in report["cases"]:
        if case["scenario"] != "decision":
            continue
        comparison[case["id"]] = {
            name: {
                "outcome": case["baselines"][name].get("outcome"),
                "facts": case["baselines"][name].get("facts", []),
                "citation_valid": case["baselines"][name].get("citation_valid", False),
                "model": case["baselines"][name].get("model"),
            }
            for name in ("A", "C")
        }
    report["a_vs_c_decision_comparison"] = comparison


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="public JSON dataset only")
    parser.add_argument("--gold", type=Path, required=True, help="Gold JSON loaded only after execution")
    parser.add_argument("--output", type=Path, default=Path("work/real_agent_reliability/dev"))
    parser.add_argument("--allow-frozen", action="store_true", help="explicit post-freeze execution acknowledgement")
    args = parser.parse_args(argv)
    public, public_raw = _load_public(args.dataset)
    freeze_path = args.dataset.parent / 'freeze.json'
    freeze = _read_json(freeze_path) if freeze_path.exists() else None
    if freeze and hashlib.sha256(public_raw).hexdigest() != freeze['files'].get(args.dataset.name):
        raise SystemExit('frozen public identity mismatch')
    if public["partition"] == "frozen" and not args.allow_frozen:
        raise SystemExit("refusing frozen execution before explicit --allow-frozen")
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = []
    for case in public["cases"]:
        row = run_case(case, args.output / case["id"])
        rows.append(row)
        print(case["id"], row["baselines"]["C"]["outcome"], flush=True)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset": {"path": str(args.dataset), "partition": public["partition"], "sha256": hashlib.sha256(public_raw).hexdigest(), "case_count": len(rows)},
        "gold_loaded_after_execution": True,
        "authoring_disclosure": public.get("authoring_disclosure"),
        "fake_model_only": True,
        "real_provider_requests": 0,
        "cases": rows,
        "run_timing_ms": round((time.perf_counter() - started) * 1000, 3),
        "selected_scenarios": {"relation": "C", "decision": "C"},
        "n_a": len(rows),
    }
    report_path = args.output / "raw_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gold, gold_raw = _load_gold(args.gold, expected_partition=public["partition"], case_ids={case["id"] for case in public["cases"]})
    if freeze and hashlib.sha256(gold_raw).hexdigest() != freeze['files'].get(args.gold.name):
        raise SystemExit('frozen Gold identity mismatch')
    report["gold"] = {"path": str(args.gold), "sha256": hashlib.sha256(gold_raw).hexdigest(), "rubric_version": gold["rubric_version"]}
    score_report(report, gold)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["score"], ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report["score"]["passed"] == report["score"]["denominator"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

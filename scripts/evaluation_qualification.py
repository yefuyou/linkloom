"""Offline, fail-closed case qualification. Never constructs a model or runs Final.

Static eligibility and executed acceptance are deliberately different records.
Exact source spans and the production plan/claim validators are reused here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linkloom.agents.claim_grounding import assess_claim_text
from linkloom.runtime.adaptive_planning import validate_source_relation_plan
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.task_plan import TaskPlan

TARGETS = {
    "normal": None, "partial": None, "conflict": None,
    "invalid_tool": "invalid_tool", "final_correction": "invalid_final_once",
    "cold_resume": "cold_resume", "provider_uncertain": "provider_uncertain",
    "workspace_isolation": "wrong_workspace", "source_freshness": "source_change",
    "budget_exhausted": "budget_exhausted",
}
RELATIONS = {"owner_of": "owner_of_project", "direct_leader_of": "reports_to",
             "reports_to": "reports_to", "alias_of": "alias_of_project"}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def public_projection(case):
    """Only trusted user input and explicit fault controls reach a controller."""
    data = deepcopy(case["input"])
    return {**{k: data[k] for k in ("query", "workspace_id", "documents", "task_plan", "budget") if k in data},
            "fault": case.get("protocol", {}).get("fault")}


def source_facts(documents):
    facts = []
    for document in documents:
        for line, text in enumerate(document.get("content", "").splitlines(), 1):
            for clause in re.split(r"(?<=[.!?。？！])\s+", text.strip().lstrip("# ")):
                ev = {"evidence_id": "preflight", "quote": clause, "status": "verified"}
                assessed = assess_claim_text(clause, ["preflight"], {"preflight": ev})
                for claim in assessed.claims:
                    relation = RELATIONS.get(claim.relation)
                    if relation and claim.status.value == "SUPPORTED":
                        facts.append({"subject": claim.subject.casefold(), "predicate": relation,
                                      "object": claim.value.casefold(), "path": document["path"],
                                      "line": line, "quote": text})
    return facts


def structural_signature(case):
    """Graph isomorphism key, independent of entity/file names, order and prose.

    Include document incidence, paragraph positions and root role; changing only
    names or shuffling statements cannot create an independent partition.
    This is an exact small-graph check, not a semantic similarity claim.
    """
    data = case["input"]
    facts = source_facts(data.get("documents", []))
    entities = sorted({f[k] for f in facts for k in ("subject", "object")})
    if len(entities) > 9:
        raise ValueError("Structural qualification supports at most nine named entities")
    root_question = (data.get("task_plan") or {}).get("subgoals", [{}])[0].get("question", "").casefold()
    roots = {v for v in entities if v in root_question}
    docs = data.get("documents", [])
    graph_forms = []
    # Entity colors by typed degree sharply bound permutations for normal cases.
    colors = {}
    for entity in entities:
        color = (entity in roots, tuple(sorted((f["predicate"], "out") for f in facts if f["subject"] == entity)),
                 tuple(sorted((f["predicate"], "in") for f in facts if f["object"] == entity)))
        colors.setdefault(color, []).append(entity)
    groups = [colors[k] for k in sorted(colors)]
    for order_groups in itertools.product(*(itertools.permutations(g) for g in groups)):
        mapping = {v: i for i, v in enumerate(itertools.chain.from_iterable(order_groups))}
        document_forms = []
        for doc in docs:
            content = doc.get("content", "")
            lines = content.splitlines()
            # Paragraph identity matters; raw line offsets/heading words do not.
            paragraph = {}; index = 0
            for line, text in enumerate(lines, 1):
                if not text.strip(): index += 1
                paragraph[line] = index
            # Canonicalize paragraph identity as a grouping, not its number or
            # order. Blank padding or moving a statement cannot mint a split.
            grouped = {}
            for fact in facts:
                if fact["path"] == doc["path"]:
                    grouped.setdefault(paragraph.get(fact["line"], 0), []).append(
                        (mapping[fact["subject"]], fact["predicate"], mapping[fact["object"]]))
            document_forms.append(sorted(sorted(edges) for edges in grouped.values()))
        graph_forms.append(json.dumps(sorted(document_forms), separators=(",", ":")))
    chain = [g.get("relation_path") for g in (data.get("task_plan") or {}).get("subgoals", [])]
    return canonical_sha({"chain": chain, "graph": min(graph_forms, default="[]")})


def _source_outcome(data, facts):
    rows = (data.get("task_plan") or {}).get("subgoals", [])
    if not rows: return None, set()
    root_question = rows[0].get("question", "").casefold()
    match = re.fullmatch(r"(?:who owns (.+)|which project does (.+) alias)\?", root_question)
    if match is None: return None, set()
    entity = (match[1] or match[2]).strip(); used = set()
    for row in rows:
        path = row.get("relation_path", [])
        if len(path) != 1: return None, used
        matching = [f for f in facts if f["subject"] == entity and f["predicate"] == path[0]]
        values = {f["object"] for f in matching}
        used.update((f["subject"], f["predicate"], f["object"]) for f in matching)
        if not values: return "partial", used
        if len(values) > 1:
            independent = any(a["object"] != b["object"] and a["path"] != b["path"]
                              for a in matching for b in matching)
            return ("conflict" if independent else "unobservable_same_source_conflict"), used
        entity = values.pop()
    return "complete", used


def qualify_bundle(bundle, *, references=()):
    errors, reports, ids, hashes, structures = [], [], {}, {}, {}
    reference_audit = []
    def fail(case, code, evidence):
        errors.append({"case_id": case.get("id", "UNKNOWN"), "code": code, "evidence": evidence})
    cases = bundle.get("cases", []) if isinstance(bundle, dict) else []
    for old in references:
        candidate = deepcopy(old)
        data = candidate.get("input", {})
        if not data.get("task_plan"):
            from linkloom.runtime.team_decision_planner import plan_relation_investigation
            plan = plan_relation_investigation(data.get("query", ""))
            if plan is not None: data["task_plan"] = plan.to_dict()
        try:
            signature = structural_signature(candidate)
            structures[signature] = (old.get("id"), "historical_exposed")
            reference_audit.append({"id": old.get("id"), "structural_sha256": signature, "status": "COMPARED"})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # Unsupported historical inputs cannot establish a graph comparison;
            # their frozen failures remain in the independent V3 audit.
            reference_audit.append({"id": old.get("id"), "status": "UNAVAILABLE", "evidence": str(exc)})
    if not isinstance(cases, list) or not cases:
        return {"status": "REJECTED", "errors": [{"case_id": None, "code": "EMPTY_CASES", "evidence": "No cases supplied"}], "cases": []}
    for case in cases:
        if not isinstance(case, dict):
            fail({}, "CASE_SHAPE_INVALID", {"value": case})
            reports.append({"id": None, "status": "REJECTED"})
            continue
        start = len(errors); identifier = case.get("id")
        data, gold, protocol = case.get("input", {}), case.get("gold", {}), case.get("protocol", {})
        if (not isinstance(data, dict) or not isinstance(gold, dict) or not isinstance(protocol, dict)
                or not isinstance(data.get("documents"), list) or not isinstance(data.get("budget"), dict)
                or not isinstance(gold.get("facts", []), list)
                or any(not isinstance(d, dict) or not isinstance(d.get("path"), str)
                       or not isinstance(d.get("content"), str) for d in data.get("documents", []))
                or any(not isinstance(f, dict) or not isinstance(f.get("supports", []), list)
                       or any(not isinstance(s, dict) for s in f.get("supports", [])) for f in gold.get("facts", []))):
            fail(case, "CASE_SHAPE_INVALID", "input/documents/budget/protocol/gold must satisfy qualification-cases/v1")
            reports.append({"id": identifier, "status": "REJECTED"})
            continue
        if case.get("partition") not in ("dev", "final"):
            fail(case, "PARTITION_INVALID", {"partition": case.get("partition"), "allowed": ["dev", "final"]})
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            fail(case, "CASE_ID_INVALID", repr(identifier))
            reports.append({"id": identifier, "status": "REJECTED"})
            continue
        if identifier in ids: fail(case, "DUPLICATE_CASE_ID", {"other": ids[identifier], "id": identifier})
        ids[identifier] = identifier
        digest = canonical_sha(data)
        if digest in hashes: fail(case, "DUPLICATE_DATA_SHA", {"sha256": digest, "other": hashes[digest]})
        hashes[digest] = identifier
        if case.get("data_sha256") not in (None, digest):
            fail(case, "DATA_SHA_MISMATCH", {"expected": case["data_sha256"], "actual": digest})
        plan = data.get("task_plan")
        if isinstance(plan, dict) and plan.get("goal") != data.get("query", "").strip():
            fail(case, "QUERY_GOAL_MISMATCH", {"query": data.get("query"), "goal": plan.get("goal")})
        budget = data.get("budget", {})
        plan_valid = True
        if any(type(budget.get(k)) is not int or budget[k] < 1 for k in ("max_steps", "max_provider_requests")):
            fail(case, "BUDGET_INVALID", {"budget": budget})
            budget = {"max_steps": 0, "max_provider_requests": 0}
        try:
            validate_source_relation_plan(plan, user_query=data.get("query", ""), max_steps=budget.get("max_steps", 0))
        except (ValidationError, TypeError, AttributeError, KeyError) as exc:
            plan_valid = False
            fail(case, "PLAN_ADMISSION_INVALID", {"message": str(exc), "plan": plan})
        if not isinstance(data.get("workspace_id"), str) or not data["workspace_id"].strip():
            fail(case, "WORKSPACE_MISSING", "Host workspace_id is required")
        documents = data.get("documents", [])
        paths = set()
        for doc in documents:
            path = doc.get("path", "")
            if not path or Path(path).is_absolute() or ".." in Path(path).parts or not path.endswith(".md") or path in paths:
                fail(case, "SOURCE_PATH_INVALID", {"path": path})
            paths.add(path)
            if doc.get("workspace_id") != data.get("workspace_id"):
                fail(case, "SOURCE_WORKSPACE_MISMATCH", {"path": path, "workspace": doc.get("workspace_id"), "host": data.get("workspace_id")})
        if any(key in data.get("source", {}) for key in ("index_sha256", "index_id")):
            fail(case, "UNMATERIALIZED_SOURCE_IDENTITY", {"source": data["source"], "required": "Scanner-owned identity, not fixture pseudo SHA"})
        target = case.get("target")
        if not isinstance(target, str) or target not in TARGETS:
            fail(case, "UNOBSERVABLE_TARGET", {"target": target, "supported": sorted(TARGETS)})
        elif protocol.get("runner") != "qualified-runtime-v1" or protocol.get("fault") != TARGETS[target]:
            fail(case, "TARGET_NOT_REACHABLE", {"target": target, "protocol": protocol, "required_fault": TARGETS[target]})
        if isinstance(target, str) and target in {"normal", "partial", "conflict", "final_correction", "cold_resume"} and isinstance(plan, dict):
            minimum = 2 * len(plan.get("subgoals", [])) + 1 + (target == "final_correction")
            if min(budget.get("max_steps", 0), budget.get("max_provider_requests", 0)) < minimum:
                fail(case, "TARGET_BUDGET_UNREACHABLE", {"minimum_model_rounds": minimum, "budget": budget})
        fault_minimum = {"invalid_tool": 2, "workspace_isolation": 2,
                         "source_freshness": 3, "provider_uncertain": 1}.get(target) if isinstance(target, str) else None
        if fault_minimum and min(budget.get("max_steps", 0), budget.get("max_provider_requests", 0)) < fault_minimum:
            fail(case, "TARGET_BUDGET_UNREACHABLE", {"minimum_model_rounds": fault_minimum, "budget": budget})
        if target == "budget_exhausted" and budget.get("max_provider_requests") != 1:
            fail(case, "BUDGET_FAULT_NOT_REACHABLE", {"required_provider_limit": 1, "budget": budget})
        facts = source_facts(documents)
        actual_outcome, used = _source_outcome(data, facts) if plan_valid else (None, set())
        if actual_outcome == "unobservable_same_source_conflict":
            fail(case, "CONFLICT_INDEPENDENT_SOURCES_REQUIRED",
                 {"required": "two distinct source paths with contradictory verified values",
                  "facts": facts})
        expected_outcome = gold.get("outcome")
        if target in ("final_correction", "cold_resume") and actual_outcome != "complete":
            fail(case, "TARGET_SOURCE_NOT_REACHABLE", {"target": target, "source_graph_outcome": actual_outcome})
        if target == "source_freshness" and isinstance(plan, dict):
            first = plan.get("subgoals", [{}])[0] if plan.get("subgoals") else {}
            if not any(f["subject"] in str(first.get("question", "")).casefold() and
                       f["predicate"] in first.get("relation_path", []) for f in facts):
                fail(case, "TARGET_SOURCE_NOT_REACHABLE", "Freshness injection requires a readable root-relation source")
        fault_outcomes = {"provider_uncertain": "provider_uncertain", "budget_exhausted": "budget_exhausted",
                          "invalid_tool": "rejected", "workspace_isolation": "rejected", "source_freshness": "rejected",
                          "final_correction": "complete", "cold_resume": "complete"}
        if isinstance(target, str) and target in fault_outcomes and expected_outcome != fault_outcomes[target]:
            fail(case, "FAULT_OUTCOME_MISMATCH", {"target": target, "expected": fault_outcomes[target], "gold": expected_outcome})
        if isinstance(target, str) and target in {"normal", "partial", "conflict"} and expected_outcome != actual_outcome:
            fail(case, "OUTCOME_SOURCE_MISMATCH", {"gold": expected_outcome, "source_graph_outcome": actual_outcome})
        gold_facts = set()
        docs = {d["path"]: d for d in documents}
        for fact in gold.get("facts", []):
            identity = tuple(str(fact.get(k, "")).casefold() for k in ("subject", "predicate", "object"))
            gold_facts.add(identity)
            supports = fact.get("supports", [])
            if not supports: fail(case, "GOLD_SUPPORT_MISSING", {"fact": fact})
            for span in supports:
                doc = docs.get(span.get("path")); begin, end = span.get("line_start"), span.get("line_end")
                lines = doc.get("content", "").splitlines() if doc else []
                valid = isinstance(begin, int) and isinstance(end, int) and 1 <= begin <= end <= len(lines)
                quote = "\n".join(lines[begin - 1:end]) if valid else None
                if quote != span.get("quote") or not valid:
                    fail(case, "GOLD_SPAN_MISMATCH", {"span": span, "actual": quote})
                if not any(tuple(f[k] for k in ("subject", "predicate", "object")) == identity and f["path"] == span.get("path")
                           and valid and begin <= f["line"] <= end for f in facts):
                    fail(case, "GOLD_FACT_UNSUPPORTED", {"fact": fact, "span": span})
        if expected_outcome in ("complete", "partial", "conflict") and gold_facts != used:
            fail(case, "GOLD_GRAPH_MISMATCH", {"gold": sorted(gold_facts), "source_reachable_facts": sorted(used)})
        # Dependencies are never prebound by the fixture. At execution they must
        # be derived again from verified reads, not this oracle-side graph.
        if data.get("dependency_entities"):
            fail(case, "PREFILLED_DEPENDENCY", {"dependency_entities": data["dependency_entities"]})
        try:
            signature = structural_signature(case)
            previous = structures.get(signature)
            if previous and previous[1] != case.get("partition"):
                fail(case, "MECHANICAL_PARTITION_LEAK", {"other_case": previous[0], "structural_sha256": signature})
            structures[signature] = (identifier, case.get("partition"))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            signature = None
            fail(case, "STRUCTURAL_AUDIT_UNAVAILABLE", str(exc))
        reports.append({"id": identifier, "status": "QUALIFIED" if len(errors) == start else "REJECTED",
                        "data_sha256": digest, "structural_sha256": signature})
    return {"schema_version": "qualification-report/v1", "status": "REJECTED" if errors else "QUALIFIED",
            "scope": "static preflight only; no model, runtime acceptance or Final execution", "provider_requests": 0,
            "errors": errors, "cases": reports, "reference_audit": reference_audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-v3", action="store_true", help="Reject mechanical copies of exposed V3 graphs")
    args = parser.parse_args()
    references = []
    if args.reference_v3:
        for partition in ("dev", "final"):
            path = ROOT / f"tests/fixtures/real_agent_contract_v3/{partition}.json"
            references.extend(json.loads(path.read_text(encoding="utf-8"))["cases"])
    report = qualify_bundle(json.loads(args.cases.read_text(encoding="utf-8-sig")), references=references)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {len(report['errors'])} qualification errors; provider requests=0")
    return int(report["status"] != "QUALIFIED")


if __name__ == "__main__":
    raise SystemExit(main())

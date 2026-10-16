"""Build and statically validate the evaluation-qualification fixture.

This file produces a small
author-visible input bundle plus a validation manifest for the main Agent's
preflight/runner work. It reuses static production validators through the
authoritative preflight; it never calls a Provider or executes a case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT_DIR = ROOT / "tests" / "fixtures" / "evaluation_qualification" / "validation"
BUNDLE_NAME = "qualification_cases.json"
MANIFEST_NAME = "validation_manifest.json"

ALLOWED_TARGETS = {
    "normal",
    "partial",
    "conflict",
    "invalid_tool",
    "final_correction",
    "cold_resume",
    "provider_uncertain",
    "workspace_isolation",
    "source_freshness",
    "budget_exhausted",
}
ALLOWED_OUTCOMES = {
    "complete",
    "partial",
    "conflict",
    "rejected",
    "provider_uncertain",
    "budget_exhausted",
}
ALLOWED_PREDICATES = {"alias_of_project", "owner_of_project", "reports_to"}
SOURCE_RELATION_FOR = {
    "alias_of_project": "alias_of",
    "owner_of_project": "owner_of",
    "reports_to": "direct_leader_of",
}
REQUIRED_FAULTS = {
    "invalid_tool": "invalid_tool",
    "final_correction": "invalid_final_once",
    "cold_resume": "cold_resume",
    "provider_uncertain": "provider_uncertain",
    "workspace_isolation": "wrong_workspace",
    "source_freshness": "source_change",
    "budget_exhausted": "budget_exhausted",
}
FAULT_OUTCOMES = {
    "invalid_tool": "rejected",
    "final_correction": "complete",
    "cold_resume": "complete",
    "provider_uncertain": "provider_uncertain",
    "workspace_isolation": "rejected",
    "source_freshness": "rejected",
    "budget_exhausted": "budget_exhausted",
}
OLD_REFERENCE_TOKENS = (
    "real_agent_contract_v3",
    "tests/fixtures/real_agent_contract_v3",
    "freeze.json",
    "regression.json",
    "idx-rac3",
    "synthetic-index-rac3",
    "vault_root_fingerprint",
)


def _doc(path: str, workspace_id: str, content: str) -> dict[str, str]:
    return {"path": path, "content": content, "workspace_id": workspace_id}


def _fact(
    subject: str,
    predicate: str,
    object_value: str,
    document: int,
    line: int,
    *,
    gold: bool = True,
    source_relation: str | None = None,
) -> dict[str, Any]:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "document": document,
        "line": line,
        "gold": gold,
        "source_relation": source_relation or SOURCE_RELATION_FOR[predicate],
    }


def _task_plan(
    query: str,
    *,
    alias: str | None = None,
    root_entity: str | None = None,
) -> dict[str, Any]:
    if alias is not None:
        return {
            "schema_version": "task-plan/v2",
            "goal": query,
            "subgoals": [
                {
                    "subgoal_id": "alias",
                    "question": f"Which project does {alias} alias?",
                    "depends_on": [],
                    "relation_path": ["alias_of_project"],
                },
                {
                    "subgoal_id": "owner",
                    "question": "Who owns {verified_entity}?",
                    "depends_on": ["alias"],
                    "relation_path": ["owner_of_project"],
                },
                {
                    "subgoal_id": "leader",
                    "question": "Who is the direct leader of {verified_entity}?",
                    "depends_on": ["owner"],
                    "relation_path": ["reports_to"],
                },
            ],
        }
    if root_entity is None:
        raise ValueError("owner/report plans require a literal root entity")
    return {
        "schema_version": "task-plan/v2",
        "goal": query,
        "subgoals": [
            {
                "subgoal_id": "owner",
                "question": f"Who owns {root_entity}?",
                "depends_on": [],
                "relation_path": ["owner_of_project"],
            },
            {
                "subgoal_id": "leader",
                "question": "Who is the direct leader of {verified_entity}?",
                "depends_on": ["owner"],
                "relation_path": ["reports_to"],
            },
        ],
    }


def _case(
    case_id: str,
    partition: str,
    target: str,
    family: str,
    query: str,
    workspace_id: str,
    documents: list[dict[str, str]],
    facts: list[dict[str, Any]],
    *,
    outcome: str,
    fault: str | None,
    budget: tuple[int, int],
    graph_shape: str,
    alias: str | None = None,
    root_entity: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "partition": partition,
        "target": target,
        "family": family,
        "query": query,
        "workspace_id": workspace_id,
        "documents": documents,
        "task_plan": _task_plan(query, alias=alias, root_entity=root_entity),
        "budget": {
            "max_steps": budget[0],
            "max_provider_requests": budget[1],
        },
        "protocol": {"runner": "qualified-runtime-v1", "fault": fault},
        "outcome": outcome,
        "facts": facts,
        "graph_shape": graph_shape,
    }


def _definitions() -> list[dict[str, Any]]:
    """Return the 16 deliberately varied, inline synthetic cases."""

    return [
        _case(
            "EQ-D01", "dev", "normal", "same_file_alias_context_two_hop",
            "Who owns Cedar Meridian and who does that owner report to?",
            "ws-eq-d01",
            [_doc(
                "cedar-meridian/brief.md", "ws-eq-d01",
                "# Cedar Meridian\n\n"
                "Mara Singh is the accountable owner of Cedar Meridian.\n\n"
                "Mara Singh reports directly to Bo Lin.\n\n"
                "CM is the team alias for Cedar Meridian.\n",
            )],
            [
                _fact("Cedar Meridian", "owner_of_project", "Mara Singh", 0, 3),
                _fact("Mara Singh", "reports_to", "Bo Lin", 0, 5),
                _fact("CM", "alias_of_project", "Cedar Meridian", 0, 7, gold=False),
            ],
            outcome="complete", fault=None, budget=(12, 12),
            graph_shape="owner@d0p2|reports@d0p3|incoming-alias@d0p4",
            root_entity="Cedar Meridian",
        ),
        _case(
            "EQ-D02", "dev", "partial", "missing_dependent_hop",
            "Who owns Moss Harbor and who does that owner report to?",
            "ws-eq-d02",
            [
                _doc(
                    "moss-harbor/status.md", "ws-eq-d02",
                    "## Moss Harbor\n\n"
                    "Talia Reed is the accountable owner of Moss Harbor.\n\n"
                    "The reporting-line record will be published in a later review.\n",
                ),
                _doc(
                    "moss-harbor/adjacent.md", "ws-eq-d02",
                    "## Adjacent program\n\n"
                    "Owen Vale owns Moss Delta.\n"
                    "Owen Vale reports directly to Priya Noor.\n",
                ),
            ],
            [
                _fact("Moss Harbor", "owner_of_project", "Talia Reed", 0, 3),
                _fact("Moss Delta", "owner_of_project", "Owen Vale", 1, 3, gold=False),
                _fact("Owen Vale", "reports_to", "Priya Noor", 1, 4, gold=False),
            ],
            outcome="partial", fault=None, budget=(8, 6),
            graph_shape="owner@d0p2|missing-dependent@d0p3|adjacent-owner-reports@d1p2",
            root_entity="Moss Harbor",
        ),
        _case(
            "EQ-D03", "dev", "conflict", "competing_current_owners",
            "Who owns Harbor Lattice and who does that owner report to?",
            "ws-eq-d03",
            [
                _doc(
                    "harbor-lattice/registry.md", "ws-eq-d03",
                    "## Current registry\n\n"
                    "Juno Park is the accountable owner of Harbor Lattice.\n",
                ),
                _doc(
                    "harbor-lattice/review.md", "ws-eq-d03",
                    "## Review note\n\n"
                    "Mira Holt is the accountable owner of Harbor Lattice.\n",
                ),
            ],
            [
                _fact("Harbor Lattice", "owner_of_project", "Juno Park", 0, 3),
                _fact("Harbor Lattice", "owner_of_project", "Mira Holt", 1, 3),
            ],
            outcome="conflict", fault=None, budget=(8, 6),
            graph_shape="owner@d0p2|owner@d1p2",
            root_entity="Harbor Lattice",
        ),
        _case(
            "EQ-D04", "dev", "normal", "alias_owner_leader_three_hop",
            "Which project does CH alias, who owns that project, and who does that owner report to?",
            "ws-eq-d04",
            [
                _doc("alias.md", "ws-eq-d04", "CH aliases Copper Haven.\n"),
                _doc("accountability.md", "ws-eq-d04", "Rhea Moss owns Copper Haven.\n"),
                _doc("reporting.md", "ws-eq-d04", "Rhea Moss reports directly to Bo Lin.\n"),
            ],
            [
                _fact("CH", "alias_of_project", "Copper Haven", 0, 1),
                _fact("Copper Haven", "owner_of_project", "Rhea Moss", 1, 1),
                _fact("Rhea Moss", "reports_to", "Bo Lin", 2, 1),
            ],
            outcome="complete", fault=None, budget=(12, 8),
            graph_shape="alias@d0p1|owner@d1p1|reports@d2p1",
            alias="CH",
        ),
        _case(
            "EQ-D05", "dev", "invalid_tool", "malformed_tool_arguments_with_distractor",
            "Who owns Silver Delta and who does that owner report to?",
            "ws-eq-d05",
            [
                _doc(
                    "silver-delta/source.md", "ws-eq-d05",
                    "# Silver Delta\n"
                    "Ivo Chen is the accountable owner of Silver Delta.\n"
                    "Ivo Chen reports directly to Ren Park.\n",
                ),
                _doc(
                    "silver-dawn/neighbor.md", "ws-eq-d05",
                    "# Neighboring program\n"
                    "Lena Cho is the accountable owner of Silver Dawn.\n",
                ),
            ],
            [
                _fact("Silver Delta", "owner_of_project", "Ivo Chen", 0, 2, gold=False),
                _fact("Ivo Chen", "reports_to", "Ren Park", 0, 3, gold=False),
                _fact("Silver Dawn", "owner_of_project", "Lena Cho", 1, 2, gold=False),
            ],
            outcome="rejected", fault="invalid_tool", budget=(8, 6),
            graph_shape="owner+reports@d0p1|distractor-owner@d1p1",
            root_entity="Silver Delta",
        ),
        _case(
            "EQ-D06", "dev", "final_correction", "compound_same_line_final_correction",
            "Who owns Quartz Gate and who does that owner report to?",
            "ws-eq-d06",
            [_doc(
                "quartz-gate/decision.md", "ws-eq-d06",
                "## Quartz Gate\n"
                "Nia Park is the accountable owner of Quartz Gate. Nia Park reports directly to Ellis Shaw.\n",
            )],
            [
                _fact("Quartz Gate", "owner_of_project", "Nia Park", 0, 2),
                _fact("Nia Park", "reports_to", "Ellis Shaw", 0, 2),
            ],
            outcome="complete", fault="invalid_final_once", budget=(12, 8),
            graph_shape="owner+reports@d0l2_same_line",
            root_entity="Quartz Gate",
        ),
        _case(
            "EQ-F01", "final", "cold_resume", "alias_fan_in_cold_resume",
            "Which project does QG alias, who owns that project, and who does that owner report to?",
            "ws-eq-f01",
            [
                _doc(
                    "quill-garden/alias.md", "ws-eq-f01",
                    "# Naming\n\nQG is an internal alias for Quill Garden.\n"
                    "GardenOps is the team alias for Quill Garden.\n",
                ),
                _doc(
                    "quill-garden/owner.md", "ws-eq-f01",
                    "# Accountable owner\n\nYara Lin owns Quill Garden.\n",
                ),
                _doc(
                    "quill-garden/line.md", "ws-eq-f01",
                    "# Reporting line\n\nYara Lin reports directly to Sol Reyes.\n",
                ),
            ],
            [
                _fact("QG", "alias_of_project", "Quill Garden", 0, 3),
                _fact("GardenOps", "alias_of_project", "Quill Garden", 0, 4, gold=False),
                _fact("Quill Garden", "owner_of_project", "Yara Lin", 1, 3),
                _fact("Yara Lin", "reports_to", "Sol Reyes", 2, 3),
            ],
            outcome="complete", fault="cold_resume", budget=(12, 12),
            graph_shape="two-alias-fan-in@d0p2|owner@d1p2|reports@d2p2",
            alias="QG",
        ),
        _case(
            "EQ-F02", "final", "normal", "chinese_owner_and_leader",
            "谁负责青岚线路？其负责人直属领导是谁？",
            "ws-eq-f02",
            [
                _doc(
                    "青岚线路/负责人.md", "ws-eq-f02",
                    "## 青岚线路\n青岚线路的负责人是沈砚。\n",
                ),
                _doc(
                    "青岚线路/汇报.md", "ws-eq-f02",
                    "## 汇报关系\n沈砚的直属领导是陆遥。\n"
                    "陆遥的直属领导是叶辰。\n",
                ),
            ],
            [
                _fact("青岚线路", "owner_of_project", "沈砚", 0, 2),
                _fact("沈砚", "reports_to", "陆遥", 1, 2),
                _fact("陆遥", "reports_to", "叶辰", 1, 3, gold=False),
            ],
            outcome="complete", fault=None, budget=(10, 6),
            graph_shape="owner@d0p1|reports@d1p1|connected-leader-spoke@d1p2|cjk-boundary",
            root_entity="青岚线路",
        ),
        _case(
            "EQ-F03", "final", "workspace_isolation", "wrong_workspace_fault_with_connected_spoke",
            "Who owns Harbor Lattice and who does that owner report to?",
            "ws-eq-f03-active",
            [
                _doc(
                    "harbor-lattice/owner.md", "ws-eq-f03-active",
                    "# Active workspace\n\nJuno Park is the accountable owner of Harbor Lattice.\n",
                ),
                _doc(
                    "harbor-lattice/line.md", "ws-eq-f03-active",
                    "# Active reporting\n\nJuno Park reports directly to Soren Vale.\n\n"
                    "Soren Vale reports directly to Talia Moss.\n",
                ),
            ],
            [
                _fact("Harbor Lattice", "owner_of_project", "Juno Park", 0, 3, gold=False),
                _fact("Juno Park", "reports_to", "Soren Vale", 1, 3, gold=False),
                _fact("Soren Vale", "reports_to", "Talia Moss", 1, 5, gold=False),
            ],
            outcome="rejected", fault="wrong_workspace", budget=(10, 6),
            graph_shape="active-owner@d0p2|active-reports@d1p2|connected-leader-spoke@d1p4|fault-wrong_workspace",
            root_entity="Harbor Lattice",
        ),
        _case(
            "EQ-F04", "final", "source_freshness", "stale_temporal_owner",
            "Who owns Copper Vale and who does that owner report to?",
            "ws-eq-f04",
            [_doc(
                "copper-vale/snapshot.md", "ws-eq-f04",
                "# Copper Vale\nAs of 2026-09-01 Lio Chen is the accountable owner of Copper Vale.\n"
                "Lio Chen owns Copper Ridge.\n",
            )],
            [
                _fact("Copper Vale", "owner_of_project", "Lio Chen", 0, 2, gold=False),
                _fact("Copper Ridge", "owner_of_project", "Lio Chen", 0, 3, gold=False),
            ],
            outcome="rejected", fault="source_change", budget=(8, 4),
            graph_shape="temporal-owner-shared-across-projects@d0p1|stale-before-final",
            root_entity="Copper Vale",
        ),
        _case(
            "EQ-F05", "final", "budget_exhausted", "budget_stop_with_connected_distractor",
            "Which project does IR alias, who owns that project, and who does that owner report to?",
            "ws-eq-f05",
            [
                _doc(
                    "indigo-reach/alias.md", "ws-eq-f05",
                    "# Historical name\nIR aliases Indigo Reach.\n",
                ),
                _doc(
                    "indigo-reach/owner.md", "ws-eq-f05",
                    "# Ownership\nRin Ito owns Indigo Reach.\n",
                ),
                _doc(
                    "indigo-reach/line.md", "ws-eq-f05",
                    "# Reporting\nRin Ito reports directly to Kai Wen.\n",
                ),
                _doc(
                    "indigo-reach/neighbor.md", "ws-eq-f05",
                    "# Adjacent project\nIR aliases Indigo Ridge.\n",
                ),
            ],
            [
                _fact("IR", "alias_of_project", "Indigo Reach", 0, 2, gold=False),
                _fact("Indigo Reach", "owner_of_project", "Rin Ito", 1, 2, gold=False),
                _fact("Rin Ito", "reports_to", "Kai Wen", 2, 2, gold=False),
                _fact("IR", "alias_of_project", "Indigo Ridge", 3, 2, gold=False),
            ],
            outcome="budget_exhausted", fault="budget_exhausted", budget=(12, 1),
            graph_shape="alias-owner-reports@d0,d1,d2|connected-alias-distractor@d3",
            alias="IR",
        ),
        _case(
            "EQ-F06", "final", "provider_uncertain", "provider_uncertain_alias_owner",
            "Which project does PM alias, who owns that project, and who does that owner report to?",
            "ws-eq-f06",
            [
                _doc(
                    "pearl-meridian/source.md", "ws-eq-f06",
                    "# Pearl Meridian\nPM is an internal alias for Pearl Meridian.\n\nOwen Brook owns Pearl Meridian.\n",
                ),
            ],
            [
                _fact("PM", "alias_of_project", "Pearl Meridian", 0, 2, gold=False),
                _fact("Pearl Meridian", "owner_of_project", "Owen Brook", 0, 4, gold=False),
            ],
            outcome="provider_uncertain", fault="provider_uncertain", budget=(12, 1),
            graph_shape="alias+owner@d0p1+p2|terminal-uncertain",
            alias="PM",
        ),
        _case(
            "EQ-F07", "final", "final_correction", "alias_owner_same_file_leader",
            "Which project does AB alias, who owns that project, and who does that owner report to?",
            "ws-eq-f07",
            [
                _doc("amber-basin/alias.md", "ws-eq-f07", "AB aliases Amber Basin.\n"),
                _doc(
                    "amber-basin/current.md", "ws-eq-f07",
                    "# Current assignment\n\n"
                    "Mara Singh is the accountable owner of Amber Basin.\n\n"
                    "Mara Singh reports directly to Bo Lin.\n",
                ),
            ],
            [
                _fact("AB", "alias_of_project", "Amber Basin", 0, 1),
                _fact("Amber Basin", "owner_of_project", "Mara Singh", 1, 3),
                _fact("Mara Singh", "reports_to", "Bo Lin", 1, 5),
            ],
            outcome="complete", fault="invalid_final_once", budget=(12, 8),
            graph_shape="alias@d0p1|owner@d1p2|reports@d1p3",
            alias="AB",
        ),
        _case(
            "EQ-F08", "final", "conflict", "conflict_with_unresolved_reporting_edge",
            "Who owns Cedar Meridian and who does that owner report to?",
            "ws-eq-f08",
            [
                _doc(
                    "cedar-meridian/registry.md", "ws-eq-f08",
                    "# Registry\n\nKira Moss is the accountable owner of Cedar Meridian.\n",
                ),
                _doc(
                    "cedar-meridian/review.md", "ws-eq-f08",
                    "# Review\n\nDane Wu is the accountable owner of Cedar Meridian.\n",
                ),
                _doc(
                    "cedar-meridian/line.md", "ws-eq-f08",
                    "# Reporting line\n\nKira Moss reports directly to Taro Lin.\n",
                ),
            ],
            [
                _fact("Cedar Meridian", "owner_of_project", "Kira Moss", 0, 3),
                _fact("Cedar Meridian", "owner_of_project", "Dane Wu", 1, 3),
                _fact("Kira Moss", "reports_to", "Taro Lin", 2, 3, gold=False),
            ],
            outcome="conflict", fault=None, budget=(10, 8),
            graph_shape="owner@d0p2|owner@d1p2|reports@d2p2|conflict-blocks-dependent",
            root_entity="Cedar Meridian",
        ),
        _case(
            "EQ-F09", "final", "invalid_tool", "host_context_mismatch_alias_chain",
            "Which project does PS alias, who owns that project, and who does that owner report to?",
            "ws-eq-f09",
            [_doc(
                "plover-station/source.md", "ws-eq-f09",
                "PS aliases Plover Station.\n"
                "Hana Moss owns Plover Station.\n"
                "Hana Moss reports directly to Yu Lan.\n",
            )],
            [
                _fact("PS", "alias_of_project", "Plover Station", 0, 1, gold=False),
                _fact("Plover Station", "owner_of_project", "Hana Moss", 0, 2, gold=False),
                _fact("Hana Moss", "reports_to", "Yu Lan", 0, 3, gold=False),
            ],
            outcome="rejected", fault="invalid_tool", budget=(10, 6),
            graph_shape="alias+owner+reports@d0p1|malformed-host-context",
            alias="PS",
        ),
        _case(
            "EQ-F10", "final", "normal", "alias_owner_leader_same_document",
            "Which project does MA alias, who owns that project, and who does that owner report to?",
            "ws-eq-f10",
            [_doc(
                "marble-arc/record.md", "ws-eq-f10",
                "# Marble Arc\n\n"
                "MA aliases Marble Arc.\n\n"
                "Jia Lee is the accountable owner of Marble Arc.\n\n"
                "Jia Lee reports directly to Dev Rao.\n",
            )],
            [
                _fact("MA", "alias_of_project", "Marble Arc", 0, 3),
                _fact("Marble Arc", "owner_of_project", "Jia Lee", 0, 5),
                _fact("Jia Lee", "reports_to", "Dev Rao", 0, 7),
            ],
            outcome="complete", fault=None, budget=(12, 8),
            graph_shape="alias@d0p2|owner@d0p3|reports@d0p4",
            alias="MA",
        ),
    ]


def _paragraph_index(content: str, line_number: int) -> int:
    lines = content.splitlines()
    return 1 + sum(1 for line in lines[: max(0, line_number - 1)] if not line.strip())


def _line_quote(document: dict[str, str], line_number: int) -> str:
    lines = document["content"].splitlines()
    if line_number < 1 or line_number > len(lines):
        raise ValueError(f"line {line_number} is outside {document['path']}")
    return lines[line_number - 1]


def _normalize_graph(case: dict[str, Any], source_facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a name-independent graph with source document/paragraph incidence."""

    names = sorted({
        str(value).casefold()
        for fact in source_facts
        for value in (fact["subject"], fact["object"])
    })
    node_ids = {name: f"n{index}" for index, name in enumerate(names)}
    edges = []
    for fact in source_facts:
        document = case["documents"][fact["document"]]
        edges.append({
            "from": node_ids[str(fact["subject"]).casefold()],
            "predicate": fact["source_relation"],
            "to": node_ids[str(fact["object"]).casefold()],
            "incidence": {
                "document_index": fact["document"],
                "paragraph_index": _paragraph_index(document["content"], fact["line"]),
                "line": fact["line"],
            },
            "active_workspace": document["workspace_id"] == case["workspace_id"],
        })
    edges.sort(key=lambda item: (
        item["predicate"], item["from"], item["to"],
        item["incidence"]["document_index"], item["incidence"]["line"],
    ))
    return {
        "nodes": [node_ids[name] for name in names],
        "edges": edges,
        "shape_contract": case["graph_shape"],
    }


def _materialize() -> tuple[dict[str, Any], dict[str, Any]]:
    definitions = _definitions()
    bundle_cases: list[dict[str, Any]] = []
    manifest_cases: list[dict[str, Any]] = []
    for case in definitions:
        bundle_facts: list[dict[str, Any]] = []
        manifest_facts: list[dict[str, Any]] = []
        for index, fact in enumerate(case["facts"], start=1):
            document = case["documents"][fact["document"]]
            quote = _line_quote(document, fact["line"])
            support = {
                "path": document["path"],
                "line_start": fact["line"],
                "line_end": fact["line"],
                "quote": quote,
            }
            evidence_id = f"{case['id']}-E{index:02d}"
            if fact["gold"]:
                bundle_facts.append({
                    "subject": fact["subject"],
                    "predicate": fact["predicate"],
                    "object": fact["object"],
                    "supports": [support],
                })
            manifest_facts.append({
                "fact_id": f"{case['id']}-F{index:02d}",
                "evidence_id": evidence_id,
                "source_relation": fact["source_relation"],
                "subject": fact["subject"],
                "object": fact["object"],
                "predicate": fact["predicate"],
                "gold": fact["gold"],
                "path": document["path"],
                "line_start": fact["line"],
                "line_end": fact["line"],
                "quote": quote,
                "workspace_id": document["workspace_id"],
            })
        bundle_cases.append({
            "id": case["id"],
            "partition": case["partition"],
            "target": case["target"],
            "input": {
                "query": case["query"],
                "workspace_id": case["workspace_id"],
                "documents": case["documents"],
                "task_plan": case["task_plan"],
                "budget": case["budget"],
            },
            "protocol": case["protocol"],
            "gold": {"outcome": case["outcome"], "facts": bundle_facts},
        })
        graph = _normalize_graph(case, case["facts"])
        canonical = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        manifest_cases.append({
            "id": case["id"],
            "partition": case["partition"],
            "family": case["family"],
            "graph_shape": case["graph_shape"],
            "graph": graph,
            "graph_signature": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "source_facts": manifest_facts,
        })

    bundle = {"schema_version": "qualification-cases/v1", "cases": bundle_cases}
    manifest = {
        "schema_version": "evaluation-qualification-validation/v1",
        "dataset": "evaluation-qualification",
        "bundle_schema_version": "qualification-cases/v1",
        "historical_context": {
            "baseline_commit": "37e45b1",
            "candidate_commit": "499bb37",
            "historical_249": {
                "baseline_total": 249,
                "baseline_pass": 209,
                "baseline_fail": 40,
                "candidate_total": 249,
                "candidate_pass": 208,
                "candidate_fail": 41,
                "status": "historical_only",
            },
        },
        "gold_visibility": "author_visible",
        "external_blind_test": False,
        "execution": {
            "classification": "STATIC_PREPARATION_ONLY",
            "final_execution": "NOT_RUN",
            "real_provider_calls": 0,
            "new_final_cases_executed": 0,
        },
        "splits": {
            "dev": [case["id"] for case in bundle_cases if case["partition"] == "dev"],
            "final": [case["id"] for case in bundle_cases if case["partition"] == "final"],
        },
        "cases": manifest_cases,
    }
    return bundle, manifest


def _walk_keys(value: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            keys.append(path)
            keys.extend(_walk_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            keys.extend(_walk_keys(child, f"{prefix}[{index}]"))
    return keys


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(bundle: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    _assert(bundle.get("schema_version") == "qualification-cases/v1", "bundle schema mismatch")
    _assert(set(bundle) == {"schema_version", "cases"}, "bundle has unexpected top-level fields")
    cases = bundle.get("cases")
    _assert(isinstance(cases, list) and len(cases) == 16, "bundle must contain exactly 16 cases")
    ids = [case.get("id") for case in cases]
    _assert(len(set(ids)) == len(ids), "case IDs must be unique")
    _assert(sum(case.get("partition") == "dev" for case in cases) == 6, "dev split must contain six cases")
    _assert(sum(case.get("partition") == "final" for case in cases) == 10, "final split must contain ten cases")
    manifest_cases = manifest.get("cases")
    _assert(isinstance(manifest_cases, list) and len(manifest_cases) == len(cases), "manifest case count mismatch")
    manifest_by_id = {case.get("id"): case for case in manifest_cases}
    _assert(set(manifest_by_id) == set(ids), "manifest IDs do not match bundle IDs")

    graph_signatures: set[str] = set()
    graph_shapes: set[str] = set()
    for case in cases:
        _assert(set(case) == {"id", "partition", "target", "input", "protocol", "gold"},
                f"{case.get('id')}: case fields mismatch")
        _assert(case["partition"] in {"dev", "final"}, f"{case['id']}: invalid partition")
        _assert(case["target"] in ALLOWED_TARGETS, f"{case['id']}: invalid target")
        input_value = case["input"]
        _assert(set(input_value) == {"query", "workspace_id", "documents", "task_plan", "budget"},
                f"{case['id']}: input fields mismatch")
        _assert(isinstance(input_value["query"], str) and input_value["query"].strip(),
                f"{case['id']}: query is empty")
        _assert(isinstance(input_value["workspace_id"], str) and input_value["workspace_id"].strip(),
                f"{case['id']}: workspace is empty")
        documents = input_value["documents"]
        _assert(isinstance(documents, list) and documents, f"{case['id']}: documents are empty")
        by_path: dict[str, dict[str, str]] = {}
        for document in documents:
            _assert(set(document) == {"path", "content", "workspace_id"},
                    f"{case['id']}: document fields mismatch")
            path = document["path"]
            _assert(isinstance(path, str) and (path.endswith(".md") or path.endswith(".txt")),
                    f"{case['id']}: document path must be a relative text path")
            _assert(not Path(path).is_absolute() and ".." not in Path(path).parts,
                    f"{case['id']}: document path escapes fixture")
            _assert(path not in by_path, f"{case['id']}: duplicate document path")
            by_path[path] = document
            _assert(isinstance(document["content"], str) and document["content"].strip(),
                    f"{case['id']}: empty document")
        task_plan = input_value["task_plan"]
        _assert(isinstance(task_plan, dict) and task_plan.get("schema_version") == "task-plan/v2",
                f"{case['id']}: task plan must be v2")
        _assert(isinstance(task_plan.get("subgoals"), list) and 2 <= len(task_plan["subgoals"]) <= 5,
                f"{case['id']}: invalid task plan subgoals")
        budget = input_value["budget"]
        _assert(set(budget) == {"max_steps", "max_provider_requests"}, f"{case['id']}: budget fields mismatch")
        _assert(all(isinstance(budget[key], int) and budget[key] >= 1 for key in budget),
                f"{case['id']}: budget values must be positive integers")
        protocol = case["protocol"]
        _assert(set(protocol) == {"runner", "fault"}, f"{case['id']}: protocol fields mismatch")
        _assert(protocol["runner"] == "qualified-runtime-v1", f"{case['id']}: runner mismatch")
        _assert(protocol["fault"] is None or isinstance(protocol["fault"], str),
                f"{case['id']}: fault must be a string or null")
        if case["target"] in REQUIRED_FAULTS:
            _assert(protocol["fault"] == REQUIRED_FAULTS[case["target"]],
                    f"{case['id']}: fault does not reach the declared target")
        else:
            _assert(protocol["fault"] is None,
                    f"{case['id']}: normal source outcome cannot carry a fault")
        gold = case["gold"]
        _assert(set(gold) == {"outcome", "facts"}, f"{case['id']}: Gold fields mismatch")
        _assert(gold["outcome"] in ALLOWED_OUTCOMES, f"{case['id']}: invalid Gold outcome")
        if case["target"] in FAULT_OUTCOMES:
            _assert(gold["outcome"] == FAULT_OUTCOMES[case["target"]],
                    f"{case['id']}: fault target and Gold outcome disagree")
        _assert(isinstance(gold["facts"], list), f"{case['id']}: Gold facts must be a list")
        for fact in gold["facts"]:
            _assert(set(fact) == {"subject", "predicate", "object", "supports"},
                    f"{case['id']}: Gold fact fields mismatch")
            _assert(fact["predicate"] in ALLOWED_PREDICATES, f"{case['id']}: unsupported Gold predicate")
            _assert(isinstance(fact["supports"], list) and fact["supports"],
                    f"{case['id']}: Gold fact has no support")
            for support in fact["supports"]:
                _assert(set(support) == {"path", "line_start", "line_end", "quote"},
                        f"{case['id']}: support fields mismatch")
                _assert(support["path"] in by_path, f"{case['id']}: support path is absent")
                _assert(isinstance(support["line_start"], int) and isinstance(support["line_end"], int),
                        f"{case['id']}: line span must be integer")
                _assert(support["line_start"] <= support["line_end"], f"{case['id']}: invalid line span")
                lines = by_path[support["path"]]["content"].splitlines()
                _assert(support["line_end"] <= len(lines), f"{case['id']}: line span exceeds document")
                exact = "\n".join(lines[support["line_start"] - 1 : support["line_end"]])
                _assert(support["quote"] == exact, f"{case['id']}: Gold quote does not match line span")
        model_keys = " ".join(_walk_keys(input_value)).casefold()
        for forbidden in ("gold", "expected", "evidence_id", "fact_id", "final_answer",
                          "provider_output", "provider_response", "provider_outcome", "stimulus"):
            _assert(forbidden not in model_keys, f"{case['id']}: model input contains {forbidden}")
        input_text = json.dumps(input_value, ensure_ascii=False).casefold()
        for forbidden in OLD_REFERENCE_TOKENS:
            _assert(forbidden.casefold() not in input_text,
                    f"{case['id']}: model input leaks old reference {forbidden}")
        subgoals = input_value["task_plan"]["subgoals"]
        paths = [tuple(goal.get("relation_path", [])) for goal in subgoals]
        _assert(tuple(paths) in [
            (("owner_of_project",), ("reports_to",)),
            (("alias_of_project",), ("owner_of_project",), ("reports_to",)),
        ], f"{case['id']}: task plan must use a supported canonical relation chain")
        first_question = subgoals[0].get("question", "")
        if paths[0][0] == "alias_of_project":
            _assert(first_question.startswith("Which project does ") and first_question.endswith(" alias?"),
                    f"{case['id']}: alias root question is not canonical")
        else:
            _assert(first_question.startswith("Who owns ") and first_question.endswith("?"),
                    f"{case['id']}: owner root question is not canonical")
        for goal in subgoals[1:]:
            _assert("{verified_entity}" in goal.get("question", ""),
                    f"{case['id']}: dependent question must use verified_entity")
        manifest_case = manifest_by_id[case["id"]]
        _assert(manifest_case["partition"] == case["partition"], f"{case['id']}: manifest partition mismatch")
        _assert(manifest_case["graph_shape"] not in graph_shapes, f"{case['id']}: duplicate graph shape")
        graph_shapes.add(manifest_case["graph_shape"])
        signature = manifest_case.get("graph_signature")
        _assert(isinstance(signature, str) and signature not in graph_signatures,
                f"{case['id']}: graph signature collision (rename leak)")
        graph_signatures.add(signature)
        evidence = manifest_case.get("source_facts")
        _assert(isinstance(evidence, list) and evidence, f"{case['id']}: source evidence is empty")
        evidence_ids = [row.get("evidence_id") for row in evidence]
        _assert(len(evidence_ids) == len(set(evidence_ids)), f"{case['id']}: evidence IDs are duplicated")
        for row in evidence:
            _assert(row.get("predicate") in ALLOWED_PREDICATES, f"{case['id']}: source predicate unsupported")
            _assert(row.get("source_relation") in {"alias_of", "owner_of", "direct_leader_of"},
                    f"{case['id']}: source relation unsupported")
            _assert(row.get("path") in by_path, f"{case['id']}: source evidence path is absent")
            lines = by_path[row["path"]]["content"].splitlines()
            _assert(row["quote"] == "\n".join(lines[row["line_start"] - 1 : row["line_end"]]),
                    f"{case['id']}: source evidence quote mismatch")
    _assert(set(manifest.get("splits", {}).get("dev", [])) == {case["id"] for case in cases if case["partition"] == "dev"},
            "manifest dev split mismatch")
    _assert(set(manifest.get("splits", {}).get("final", [])) == {case["id"] for case in cases if case["partition"] == "final"},
            "manifest final split mismatch")
    execution = manifest.get("execution", {})
    _assert(execution.get("final_execution") == "NOT_RUN", "new Final execution must remain NOT_RUN")
    _assert(execution.get("real_provider_calls") == 0, "qualification preparation must have zero Provider calls")
    _assert(execution.get("new_final_cases_executed") == 0, "new Final cases must not execute")
    _assert(manifest.get("gold_visibility") == "author_visible", "Gold visibility must be explicit")
    _assert(manifest.get("external_blind_test") is False, "preparation must not claim a blind test")
    return {
        "status": "PASS",
        "cases": len(cases),
        "dev": sum(case["partition"] == "dev" for case in cases),
        "final": sum(case["partition"] == "final" for case in cases),
        "unique_graph_signatures": len(graph_signatures),
        "gold_visibility": manifest["gold_visibility"],
        "final_execution": execution["final_execution"],
        "real_provider_calls": execution["real_provider_calls"],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check existing artifacts without writing them")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    bundle_path = output_dir / BUNDLE_NAME
    manifest_path = output_dir / MANIFEST_NAME
    if args.check:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        bundle, manifest = _materialize()
    report = validate(bundle, manifest)
    from scripts.evaluation_qualification import qualify_bundle
    references = []
    for partition in ("dev", "final"):
        old = ROOT / f"tests/fixtures/real_agent_contract_v3/{partition}.json"
        references.extend(json.loads(old.read_text(encoding="utf-8"))["cases"])
    preflight = qualify_bundle(bundle, references=references)
    if preflight["status"] != "QUALIFIED":
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 1
    report["authoritative_preflight"] = preflight["status"]
    if not args.check:
        # Validate the fully materialized records before creating either output.
        # A failed qualification must never leave a partially frozen bundle.
        _write_json(bundle_path, bundle)
        _write_json(manifest_path, manifest)
    report["bundle"] = str(bundle_path)
    report["manifest"] = str(manifest_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

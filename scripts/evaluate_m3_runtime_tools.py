"""Reproduce M3-B synthetic FakeModel wiring, never a model capability score.

Uses existing public P3 fixtures/constructors via test support. No M3 Gold,
scorer, SemanticProposal, network, or live provider is imported or executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tests.integration.test_task_relation_runtime import prepared, execute
from tests.unit.test_task_relation_query import retract


def write(path, data):
    with path.open("x", encoding="utf8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": "m3-runtime-tools-evidence/v1",
               "execution_kind": "FAKE_MODEL_SCRIPTED_TOOL_ACTIONS",
               "claims": {"runtime_tool_wiring": True, "real_model_autonomous_planning": False,
                          "llm_semantic_proposals_connected": False, "m3_business_score": None},
               "scenarios": []}
    for variant in ("verified_rules", "no_records", "stale", "revoked", "insufficient"):
        root = output / variant
        tools, path, snapshot, rows = prepared(root, "D07" if variant == "insufficient" else "D04")
        if variant == "no_records":
            from linkloom.tools.task_relations import TaskRelationTools
            tools = TaskRelationTools(snapshot.workspace_id, lambda: snapshot, relation_path=root / "absent.sqlite")
        if variant == "stale":
            span = snapshot.observations[0].provenance
            snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref,
                content_hash=span.content_hash, source_version="synthetic-replaced-version")
        if variant == "revoked":
            retract(path, snapshot.workspace_id, rows[0]["same_task"]["assessment_id"])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        source_before = [(a.artifact_version_id, hashlib.sha256(a.content.encode("utf8")).hexdigest())
                         for a in snapshot.artifacts]
        status, state, result, model = execute(root, tools)
        write(root / "checkpoint.json", state.to_dict())
        write(root / "status.json", status.to_dict())
        write(root / "model-observations.json", [r.to_dict() for r in model.requests])
        payload = result.get("result", {}).get("task_relations", {}) if result.get("result") else {}
        query = payload.get("relation_query", {})
        summary["scenarios"].append({"scenario": variant, "status": status.status,
            "run_id": status.run_id, "thread_id": status.thread_id, "checkpoint_id": status.checkpoint_id,
            "checkpoint": f"{variant}/checkpoint.json", "result": f"{variant}/checkpoints/{state.result_ref}",
            "trace": f"{variant}/traces/{status.run_id}/events.jsonl",
            "review": result.get("review"), "query_status": query.get("status"),
            "model_requests": model.call_count, "tool_ledger": [r.tool_id for r in state.tool_ledger],
            "sources_unchanged": source_before == [(a.artifact_version_id,
                hashlib.sha256(a.content.encode("utf8")).hexdigest()) for a in snapshot.artifacts],
            "vault_anchor_unchanged": (root / "vault" / "synthetic.md").read_text(encoding="utf8")
                == "# Synthetic runtime anchor\n",
            "sidecar_sha256_before": before,
            "sidecar_sha256_after": hashlib.sha256(path.read_bytes()).hexdigest(),
            "missing_database_created": (root / "absent.sqlite").exists(),
            "current_supported": sum(r["current_supported"] for r in query.get("relations", [])),
            "error": status.error})
    summary["passed"] = all(row["status"] == "completed"
        and row["review"]["decision"] == "evidence_sufficient"
        and row["sidecar_sha256_before"] == row["sidecar_sha256_after"]
        and row["sources_unchanged"] and row["vault_anchor_unchanged"]
        and not row["missing_database_created"] for row in summary["scenarios"])
    write(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory, exclusive create")
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps({"passed": summary["passed"], "scenarios": len(summary["scenarios"]),
                      "execution_kind": summary["execution_kind"]}))
    raise SystemExit(0 if summary["passed"] else 1)

"""Reproduce offline M3-D evidence. No Gold, holdout, scorer or live Provider.

Run with PYTHONPATH=src;. from the checkout and a new --output directory.
Full Runtime artifacts remain under that directory. Portable evidence exports
contain synthetic sources and opaque identities; checkpoint paths are omitted.
"""
import argparse
import hashlib
import json
from pathlib import Path

from evals.m3_task_impact.fixtures import load_inputs
from evals.m3_task_impact.runner import execute as execute_dev
from evals.m3_task_impact.semantic_runtime_adapter import OfflineSemanticRuntimeAdapter
from linkloom.evaluation.task_semantic_replay import load_recorded_semantic_adapter
from linkloom.tools.semantic_analysis import SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from tests.semantic_integration_support import setup, execute
from tests.integration.test_semantic_runtime_boundaries import variant, variant_arguments
from tests.unit.test_task_relation_semantic import FakeModel

REPLAY = Path("docs/requirements/m3-llm-semantics/artifacts")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def source_hashes(context):
    return {a.artifact_version_id: hashlib.sha256(a.content.encode("utf8")).hexdigest() for a in context.artifacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    exports = args.output / "portable"
    cases = []
    outer_calls = 0
    frozen_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in REPLAY.iterdir() if p.is_file()}
    for name in ("revision", "causality", "conflict", "variant-complete", "variant-missing-refuted", "variant-missing-unknown"):
        replay = not name.startswith("variant-")
        snapshot = None
        if replay:
            adapter = load_recorded_semantic_adapter(REPLAY, name)
        else:
            context = variant()
            snapshot = SemanticSourceSnapshot.from_context(context)
            omission = "REFUTED" if name.endswith("refuted") else "UNKNOWN" if name.endswith("unknown") else None
            adapter = FakeModel(variant_arguments(context, omission))
        root = args.output / name
        engine, outer, inner, _, original = setup(root, name if replay else "causality", snapshot=snapshot,
            adapter=adapter, policy=SemanticCallPolicy("REPLAY" if replay else "FAKE"))
        context = original if replay else context
        before = source_hashes(context)
        anchor = (root / "vault/synthetic.md").read_bytes()
        status, state, result = execute(engine, root, context.question)
        assert status.status == "completed", status.to_dict()
        payload = result["result"]["task_relations"]
        semantic = payload["semantic_analysis"]
        assert semantic["analysis_status"] == "EXECUTED"
        assert len(inner.requests) == 1 and outer.call_count == 4
        outer_calls += outer.call_count
        assert all(not p["publishable"] for p in semantic["proposals"])
        rows = [dict(predicate=p["predicate"], verdict=p["proposed_verdict"], stage=p["stage"],
            evidence=p["evidence_status"], coverage=p["coverage"], semantic_status=p["semantic_status"],
            publishable=p["publishable"], comparison=p.get("comparison")) for p in semantic["proposals"]]
        record = dict(case_id=name, execution_kind="REPLAY" if replay else "FAKE", runtime_status=status.status,
            investigation_status=payload["investigation_status"], outer_model=dict(kind="FAKE", calls=outer.call_count,
                budget=state.policy.to_dict(), records=[r.to_dict() for r in state.model_executions]),
            inner_model=semantic["execution"], proposals=rows, reviewer=result["review"],
            readonly=dict(source_hashes_before=before, source_hashes_after=source_hashes(context),
                synthetic_anchor_unchanged=anchor == (root / "vault/synthetic.md").read_bytes(),
                p4_created=any(root.rglob("*relations*.sqlite"))),
            checkpoint=dict(run_id=state.run_id, thread_id=state.thread_id, status=state.status,
                model_executions=[r.to_dict() for r in state.model_executions],
                tool_ledger=[r.to_dict() for r in state.tool_ledger], termination=state.termination.to_dict()),
            quality="NOT_EVALUATED; script chooses outer tools; replay imports old public model output")
        save(exports / name / "runtime.json", record)
        save(exports / name / "result.json", result)
        # Preserve exact bytes so result ArtifactRef hashes remain verifiable.
        for path in (root / "checkpoints/models").rglob("*.json"):
            if "semantic" in path.parts:
                dest = exports / name / "models" / path.relative_to(root / "checkpoints/models")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("xb") as stream: stream.write(path.read_bytes())
        cases.append({k: v for k, v in record.items() if k not in {"checkpoint", "outer_model"}})
    dev_adapter = OfflineSemanticRuntimeAdapter(args.output / "dev-D01")
    dev = []
    for case in load_inputs():
        record = execute_dev(case, dev_adapter if case["case_id"] == "D01" else None,
            adapter_kind="FAKE_RUNTIME_NOT_EVALUATED" if case["case_id"] == "D01" else "UNAVAILABLE")
        record["evaluation_status"] = "NOT_EVALUATED" if case["case_id"] == "D01" else "NOT_RUN"
        dev.append(record)
    save(exports / "dev-contract.json", dict(records=dev, D01_runtime_result=dev_adapter.last["result"],
        D01_outer_fake_calls=dev_adapter.last["outer_fake_calls"], new_live_provider_requests=0,
        limitation="D01 execution contract only; M1/typed-question discovery missing; other 11 NOT_RUN; no scoring"))
    frozen_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in REPLAY.iterdir() if p.is_file()}
    assert frozen_before == frozen_after
    save(exports / "summary.json", dict(schema="m3d-offline-evidence/v1", cases=cases,
        outer_fake_calls=outer_calls, dev_outer_fake_calls=dev_adapter.last["outer_fake_calls"],
        inner_replay_adapter_requests=sum(c["inner_model"]["adapter_requests"] for c in cases if c["execution_kind"] == "REPLAY"),
        inner_fake_adapter_requests=sum(c["inner_model"]["adapter_requests"] for c in cases if c["execution_kind"] == "FAKE"),
        new_live_provider_requests=0, new_billable_cost_usd=0.0, imported_historical_cost_usd=None,
        semantic_acceptance="NOT_EVALUATED", business_scores="NOT_EVALUATED", production_agent_quality="NOT_EVALUATED",
        frozen_A_unchanged=True, frozen_A_file_hashes=frozen_after, dev_executed=["D01"], dev_remaining_not_run=11,
        artifact_scope="synthetic public sources; no SDK secrets; checkpoint host paths omitted; exact inner bytes preserved"))
    print(json.dumps(dict(output=str(args.output), runtime_cases=len(cases), new_live_provider_requests=0, dev_quality="NOT_EVALUATED")))


if __name__ == "__main__":
    main()

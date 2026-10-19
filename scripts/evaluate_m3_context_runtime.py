"""Offline M3-E Runtime evidence; public synthetic sources, no Gold or live API.

Write to a NEW --output directory. Portable ZIPs preserve exact Runtime artifacts
and their hashes; JSON exports distinguish reference token counts from billing.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import zipfile

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.context.policy import ContextPolicy
from linkloom.context.working import WorkingContext
from linkloom.evaluation.task_semantic_replay import load_recorded_semantic_adapter
from linkloom.runtime.context_engineering import ContextRuntimeConfig
from linkloom.runtime.graph import RuntimeEngine
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticCallPolicy
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from tests.semantic_integration_support import setup, execute


REPLAY = Path("docs/requirements/m3-llm-semantics/artifacts")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def reference_tokens(text):
    return (len(text.encode("utf8")) + 3) // 4


def config():
    return ContextRuntimeConfig(policy=ContextPolicy(model_id="OFFLINE_REFERENCE_ONLY", context_window_tokens=65536,
        output_reserve_tokens=2048, token_counter=reference_tokens,
        token_count_kind="INJECTED_UTF8_QUADS_REFERENCE; NOT_GEMINI_OR_BILLING"), tokenizer_id="OFFLINE_UTF8_QUADS_V1")


def long_model(workspace):
    ids = {}
    def inspect(r): return ModelAction.tool(ToolCall("inspect", "inspect_task_context", {"workspace_id": workspace}))
    def query(r):
        ids["context_call_id"] = r.observation.call_id
        return ModelAction.tool(ToolCall("query", "query_task_relations", {"workspace_id": workspace}))
    def analyze(r):
        ids["relations_call_id"] = r.observation.call_id
        return ModelAction.tool(ToolCall("analyze", "analyze_task_semantics", {"workspace_id": workspace, "investigation_id": "investigation"}))
    def final(r): return ModelAction.final(json.dumps({**ids, "semantic_call_id": r.observation.call_id}))
    return FakeModelAdapter([*([inspect] * 6), query, analyze, final])


def verify_archive(path, expected_sha256):
    """Read-only verification of portable exact artifacts and their identities."""
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("artifact-hashes.json"))
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert set(manifest) == {n for n in archive.namelist() if n.startswith("models/")}
        assert all(hashlib.sha256(archive.read(n)).hexdigest() == h for n, h in manifest.items())
        def read(ref, digest):
            data = archive.read("models/" + ref)
            assert hashlib.sha256(data).hexdigest() == digest
            return json.loads(data)
        state = json.loads(archive.read("final-checkpoint.json"))
        records = state["model_executions"]
        assert len({r["turn_id"] for r in records}) == len(records)
        assert len({r["call_id"] for r in state["tool_ledger"]}) == len(state["tool_ledger"])
        for record in records:
            request = read(record["request_ref"], record["request_sha256"])
            identity = {k: record[k] for k in ("run_id", "turn_id", "task_id", "agent_id", "sequence")}
            assert request["runtime_identity"] == identity
            if record["response_ref"]:
                read(record["response_ref"], record["response_sha256"])
            pointer = request["context_audit"]["engineering"]["audit_ref"]
            audit = read(pointer["ref"], pointer["sha256"])
            assert audit["status"] == "COMPLETE"
            assert audit["prepared_request_sha256"] == canonical_hash(request["model_request"])
            assert audit["run_id"] == record["run_id"] and audit["turn_id"] == record["turn_id"]
            body = json.dumps(request["model_request"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")
            budget = audit["budget"]
            assert budget["input_bytes"] == len(body) + audit["config"]["envelope"]["bytes"]
            assert budget["input_tokens"] == (len(body) + 3) // 4 + audit["config"]["envelope"]["tokens"]
            assert budget["input_tokens"] + budget["output_reserve_tokens"] <= budget["context_window_tokens"]
            assert budget["input_bytes"] <= budget["max_request_bytes"] <= 96 * 1024
            assert budget["user_input_bytes"] <= 32 * 1024 and not budget["actual_provider_tokens"]
            assert all(c["complete"] for c in audit["coverage"])
            before = WorkingContext.from_json(read(audit["before_working_ref"]["ref"], audit["before_working_ref"]["sha256"])["working_json"])
            after = WorkingContext.from_json(read(audit["working_ref"]["ref"], audit["working_ref"]["sha256"])["working_json"])
            assert replace(before, operations=(), summary_text="", summary_evidence_ids=()) == replace(after, operations=(), summary_text="", summary_evidence_ids=())
            ledger = read(after.ledger_ref.locator, after.ledger_ref.sha256)
            snapshot = read(after.checkpoint_ref.locator, after.checkpoint_ref.sha256)["state"]
            assert after.run_id == state["run_id"] == ledger["run_id"] == snapshot["run_id"]
            assert snapshot["tool_ledger"] == ledger["records"] == state["tool_ledger"][:len(ledger["records"])]
            assert snapshot["model_executions"] == records[:len(snapshot["model_executions"])]
            mapping = read(audit["mapping_ref"]["ref"], audit["mapping_ref"]["sha256"])["entries"]
            assert [r.to_dict() for r in after.evidence_refs] == [r["context_ref"] for r in mapping]
            assert all(r["authorized_snapshot_id"] == audit["authorized_snapshot_id"] for r in mapping)
        return dict(archive=path.name, status="PASS", model_artifacts=len(manifest), outer_records=len(records), tool_records=len(state["tool_ledger"]))


def export(root, output, state, result, label, outer_calls, inner, expected):
    model_root = root / "checkpoints/models"
    audits = []
    for record in state.model_executions:
        payload = json.loads((model_root / record.request_ref).read_text(encoding="utf8"))
        audit = payload["context_audit"]["engineering"]
        assert audit["status"] == "COMPLETE"
        assert audit["budget"]["input_bytes"] <= audit["budget"]["max_request_bytes"]
        before = WorkingContext.from_json(json.loads((model_root / audit["before_working_ref"]["ref"]).read_text(encoding="utf8"))["working_json"])
        after = WorkingContext.from_json(json.loads((model_root / audit["working_ref"]["ref"]).read_text(encoding="utf8"))["working_json"])
        protected_before = json.loads(before.to_json())
        protected_after = json.loads(after.to_json())
        for key in ("operations", "summary_text", "summary_evidence_ids"):
            protected_before.pop(key); protected_after.pop(key)
        assert protected_before == protected_after
        audits.append(dict(sequence=record.sequence, **audit, before_working=json.loads(before.to_json()),
            after_working=json.loads(after.to_json()), protected_state_unchanged=True))
    if result.get("working_context_audit", {}).get("status") == "BLOCKED":
        audits.append(result["working_context_audit"])
    archive_path = output / (label + ".zip")
    manifest = {}
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(model_root.rglob("*.json")):
            name = "models/" + path.relative_to(model_root).as_posix()
            data = path.read_bytes()
            manifest[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
        archive.writestr("final-checkpoint.json", json.dumps(state.to_dict(), ensure_ascii=False))
        archive.writestr("result.json", json.dumps(result, ensure_ascii=False))
        for path in sorted((root / "traces").rglob("*.jsonl")):
            archive.writestr("trace/" + path.name, path.read_bytes())
        archive.writestr("artifact-hashes.json", json.dumps(manifest, sort_keys=True))
    with zipfile.ZipFile(archive_path) as archive:
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest.items())
    semantic = (result.get("result") or {}).get("task_relations", {}).get("semantic_analysis")
    record = dict(case_id=label, expected=expected, actual_runtime_status=state.status, outer_fake_calls=outer_calls,
        inner_adapter_requests=len(inner.requests), inner_execution=semantic["execution"] if semantic else None,
        new_live_provider_requests=0, business_semantic_acceptance="NOT_EVALUATED", publishable=False,
        reviewer=result.get("review"), error=result.get("error"), audits=audits,
        archive=dict(path=archive_path.name, sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            exact_model_artifact_count=len(manifest), hash_validation="PASS"),
        frozen_metrics="A/C/D HISTORICAL ONLY; NOT RERUN_OR_OVERWRITTEN; NO_NEW_BUSINESS_SCORE")
    save(output / (label + ".json"), record)
    return dict(case_id=label, status=state.status, outer_fake_calls=outer_calls, inner_adapter_requests=len(inner.requests),
        compaction_events=sum(bool(a.get("compaction")) for a in audits),
        dropped_history=sum((a.get("compaction") or {}).get("dropped_history", 0) for a in audits),
        min_required_coverage=min((sum(c["complete"] for c in a["coverage"]) / len(a["coverage"]) for a in audits if a["coverage"]), default=None),
        recovery_failures=[a["errors"] for a in audits if a.get("errors")], archive=record["archive"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path, help="read-only verify an existing portable evidence directory")
    args = parser.parse_args()
    if args.verify:
        summary = json.loads((args.verify / "summary.json").read_text(encoding="utf8"))
        verified = [verify_archive(args.verify / c["archive"]["path"], c["archive"]["sha256"]) for c in summary["cases"]]
        print(json.dumps(dict(status="PASS", cases=verified, new_live_provider_requests=0)))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    portable = args.output / "portable"
    portable.mkdir()
    protected = [Path("docs/requirements") / folder for folder in
        ("m3-llm-semantics/artifacts", "m3-context-engineering", "m3-semantic-integration/evidence")]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for d in protected for p in d.rglob("*") if p.is_file()}
    summary = []
    for label in ("revision-replay", "causality-replay", "conflict-replay", "long-history", "source-stale", "budget-blocked", "cold-final"):
        name = label.split("-")[0] if label.endswith("replay") else "revision"
        replay = label.endswith("replay")
        adapter = load_recorded_semantic_adapter(REPLAY, name) if replay else None
        root = args.output / label
        engine, outer, inner, live, context = setup(root, name, adapter=adapter,
            policy=SemanticCallPolicy("REPLAY" if replay else "FAKE"))
        engine._context_runtime_config = config()
        source_hashes = {a.artifact_version_id: hashlib.sha256(a.content.encode("utf8")).hexdigest() for a in context.artifacts}
        if label == "long-history":
            outer = long_model(context.workspace_id); engine.model = outer
            engine._context_runtime_config = replace(config(), history_soft_bytes=8000)
        if label == "budget-blocked":
            engine._context_runtime_config = replace(config(), policy=replace(config().policy, max_request_bytes=1024))
        if label == "source-stale":
            def inspect(r): return ModelAction.tool(ToolCall("i", "inspect_task_context", {"workspace_id": context.workspace_id}))
            def stale(r):
                span = context.allowed_spans[0][1]
                live["snapshot"].source_registry.register_evidence(context.workspace_id, span.evidence_ref,
                    content_hash=span.content_hash, source_version="synthetic-updated-source")
                return ModelAction.tool(ToolCall("q", "query_task_relations", {"workspace_id": context.workspace_id}))
            outer = FakeModelAdapter([inspect, stale, ModelAction.final("must not run")]); engine.model = outer
        if label == "cold-final":
            saved = engine.checkpointer.save
            stopped = {}
            def crash(state, *a, **kw):
                ref = saved(state, *a, **kw)
                if len(state.model_executions) == 4 and state.model_executions[-1].status == "response_durable":
                    stopped["state"] = state
                    raise KeyboardInterrupt("synthetic durable Final crash")
                return ref
            engine.checkpointer.save = crash
            try: execute(engine, root, context.question)
            except KeyboardInterrupt: pass
            engine.checkpointer.save = saved
            state = stopped["state"]
            original_bytes = {p: p.read_bytes() for p in (root / "checkpoints/models").rglob("*.json")}
            remaining = FakeModelAdapter([ModelAction.final("must not run")])
            cold = RuntimeEngine(engine.vault_root, engine.index_path, engine.checkpoint_dir, trace_dir=engine.trace_dir,
                model=remaining, task_relation_tools=engine._task_relation_tools, context_runtime_config=engine._context_runtime_config,
                memory_enabled=False, decision_memory_enabled=False)
            status = cold.resume_multi_agent(state.thread_id)
            assert remaining.call_count == 0 and len(inner.requests) == 1
            assert all(p.read_bytes() == data for p, data in original_bytes.items())
            state = cold.checkpointer.get_latest(state.thread_id)
            result = json.loads((root / "checkpoints" / state.result_ref).read_text(encoding="utf8"))
        else:
            status, state, result = execute(engine, root, context.question, steps=9 if label == "long-history" else 4)
        expected = "failed" if label in {"source-stale", "budget-blocked"} else "completed"
        assert status.status == expected, (label, status.to_dict())
        assert source_hashes == {a.artifact_version_id: hashlib.sha256(a.content.encode("utf8")).hexdigest() for a in context.artifacts}
        summary.append(export(root, portable, state, result, label, outer.call_count, inner, expected))
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for d in protected for p in d.rglob("*") if p.is_file()}
    save(portable / "summary.json", dict(schema="m3e-runtime-evidence/v1", cases=summary,
        live_provider_requests=0, business_success_rate="NOT_EVALUATED", token_counter=config().policy.token_count_kind,
        protected_frozen_files_unchanged=True, protected_hashes=before,
        data="PUBLIC_SYNTHETIC_ONLY; exact audit ZIPs contain only isolated synthetic source/checkpoint paths"))
    print(json.dumps(dict(output=str(args.output), cases=len(summary), live_provider_requests=0)))


if __name__ == "__main__": main()

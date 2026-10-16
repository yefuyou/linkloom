"""Public-only V2 execution on the original Runtime; no global monkeypatches."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]

from linkloom.agents.claim_grounding import assess_claim_text, ClaimSupportStatus
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.runtime.task_plan import assess_task_plan, verified_dependency_entities
from linkloom.runtime.errors import ValidationError
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from scripts.self_improvement.policy import ExperimentalQueryStrategy, controller_type
from scripts.self_improvement.v2_contracts import parse_candidate, write, sha
from run_bounded_multihop import SourceOnlyController


class BackendObserver:
    def __init__(self, delegate, rows):
        self.delegate, self.rows = delegate, rows

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def search(self, query, **kwargs):
        result = self.delegate.search(query, **kwargs)
        self.rows.append({"query": query, "top_k": kwargs["top_k"],
                          "candidate_count": result.observation.candidate_count,
                          "returned": result.evidence})
        return result


class StrategyRuntimeEngine(RuntimeEngine):
    """Opt-in lab composition seam. Original ToolRuntime and reviewers are reused."""
    def __init__(self, *args, strategy, **kwargs):
        self.strategy, self.search_observations = strategy, []
        super().__init__(*args, **kwargs)

    def _build_runtime_agent_adapter(self):
        adapter = super()._build_runtime_agent_adapter()
        adapter._subgoal_query_strategy = ExperimentalQueryStrategy(self.strategy.query_mode)
        adapter._retrieval_backend = BackendObserver(adapter._retrieval_backend, self.search_observations)
        return adapter


class ObservedModel:
    def __init__(self, delegate, candidate, *, real):
        self.delegate, self.candidate, self.real = delegate, candidate, real
        self.requests = []

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def complete(self, request):
        if self.real and self.candidate.read_mode != "all":
            # Search quotes are not exposed as verified evidence by the Provider.
            # A hint cannot reproduce the FakeController's private pending list.
            hint = ("Experimental, non-authoritative reading preference: prioritize relation-relevant "
                    "search candidates, but verify potentially conflicting sources. Search results "
                    "are not evidence. Never invent dependency entities or relax any validation. "
                    "Use search limit " + str(self.candidate.limit) + ".")
            request = replace(request, recovery_guidance=(request.recovery_guidance or "") + " " + hint)
        self.requests.append(request.to_dict())
        return self.delegate.complete(request)


class ObservedFakeModel(ObservedModel):
    complete = None

    def decide(self, request):
        self.requests.append(request.to_dict())
        return self.delegate.decide(request)


def execute(case, candidate, *, model=None, real=False, work_root=None):
    allowed = {"id", "query", "workspace_id", "as_of", "documents", "scenario", "max_steps"}
    if set(case) - allowed or not {"id", "query", "workspace_id", "documents"} <= set(case):
        raise ValueError("Public case schema contains oracle or unsupported fields")
    started = time.monotonic()
    plan = plan_relation_investigation(case["query"])
    result = {"id": case["id"], "candidate_id": candidate.id, "mode": "REAL_GEMINI" if real else "FAKE_CONTROLLER",
              "outcome": "planner_unsupported", "reason": "planner_unsupported", "values": [],
              "sources": [], "calls": [], "search_observations": [], "reads": [],
              "final": None, "run_status": None, "error": None, "reviewer": None,
              "claim_supported": True, "citation_valid": True, "bindings_verified": True,
              "workspace_valid": True, "source_identity_valid": True, "version_valid": True,
              "model_turns": 0, "context_bytes": 0, "context_total_bytes": 0,
              "search_candidate_count": 0, "verified_read_count": 0,
              "real_provider_requests": 0, "elapsed_ms": 0,
              "transfer": {"host_query": True, "read_routing": "advisory_only" if real else "controller_enforced",
                           "production_default_changed": False}}
    if plan is None:
        return result
    root = work_root or ROOT / "work/r2" / uuid4().hex[:7]
    vault = root / "v"
    vault.mkdir(parents=True)
    for document in case["documents"]:
        if document["workspace_id"] != case["workspace_id"]:
            continue
        path = Path(document["path"])
        if path.name != document["path"] or path.suffix != ".md":
            raise ValueError("Only synthetic Markdown basenames are accepted")
        (vault / path).write_text(document["content"], encoding="utf-8")
    index = scan_vault(vault, root / "i").index_path
    fault = case.get("scenario", {}).get("kind", "none")
    fault_applied = []
    if model is None:
        policy = controller_type(SourceOnlyController, candidate)

        class Controller(policy):
            def __call__(self, request):
                action = super().__call__(request)
                if action.kind != "tool_call" or fault_applied:
                    return action
                call = action.tool_call
                arguments = dict(call.arguments)
                if fault in {"source_update", "stale_evidence"} and call.tool_id == "read_verified_note":
                    path = next(vault.glob("*.md"))
                    path.write_text(path.read_text(encoding="utf-8") + "\nSynthetic source revision.", encoding="utf-8")
                    fault_applied.append(fault)
                if fault == "forged_dependency" and call.tool_id == "search_notes" and arguments["source_context"].get("dependency_entities"):
                    context = dict(arguments["source_context"])
                    context["dependency_entities"] = {key: "Invented Person" for key in context["dependency_entities"]}
                    arguments["source_context"] = context
                    fault_applied.append(fault)
                if fault == "invalid_tool":
                    fault_applied.append(fault)
                    return ModelAction.tool(ToolCall(call_id=call.call_id, tool_id="delete_note", arguments={}))
                return ModelAction.tool(ToolCall(call_id=call.call_id, tool_id=call.tool_id, arguments=arguments))

        model = FakeModelAdapter([Controller()] * 20)
    observed = (ObservedModel if real else ObservedFakeModel)(model, candidate, real=real)
    engine = StrategyRuntimeEngine(vault_root=vault, index_path=index, checkpoint_dir=root / "c",
        trace_dir=root / "t", checkpointer=InMemoryCheckpointer(), model=observed,
        memory_enabled=False, decision_memory_enabled=False, strategy=candidate)
    status = engine.start_multi_agent(RunRequest(request_id="v2", thread_id="v2", workflow="team_decision",
        query=case["query"], task_plan=plan.to_dict(), max_steps=case.get("max_steps", 20),
        max_provider_requests=20, dry_run=True))
    state = engine.checkpointer.get_latest("v2")
    records = state.tool_ledger if state else []
    call_rows, bindings_valid = [], True
    for record in records:
        row = {**record.to_dict(), "tool": record.tool_id}
        if record.tool_id == "search_notes":
            context = record.arguments.get("source_context", {})
            row.update(hop=context.get("subgoal_id"), variant=context.get("query_variant", "primary"),
                       query=record.arguments.get("query"))
            if record.status == "completed":
                row["returned_count"] = len((record.result or {}).get("value", []))
                prior = [item for item in records if item.sequence < record.sequence]
                assessment = assess_task_plan(plan, prior, run_id=record.run_id, task_id=record.task_id,
                    agent_id=record.agent_id, expected_workspace_id=state.source.vault_root_fingerprint)
                try:
                    verified_dependency_entities(plan, assessment, context["subgoal_id"], context.get("dependency_entities", {}))
                except ValidationError:
                    bindings_valid = False
        if record.tool_id == "read_verified_note" and record.status == "completed":
            value = (record.result or {}).get("value", {})
            row.update(evidence_id=value.get("evidence_id"), source=value.get("relative_path"))
        call_rows.append(row)
    reads = [record.result["value"] for record in records if record.tool_id == "read_verified_note"
             and record.status == "completed" and record.result and record.result.get("status") == "ok"]
    payload_path = root / "c/results" / status.run_id / "result.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8")) if payload_path.exists() else {}
    output = payload.get("result") or {}
    final = output.get("team_decision")
    by_ref = {row["evidence_id"]: row for row in reads}
    values, claims, supported = [], [], True
    if final:
        texts = ([final["decision"]["value"]] if final["decision"]["value"] else
                 [item["point"] for item in final["rationale"]])
        for text in texts:
            assessment = assess_claim_text(text, final["evidence_refs"], by_ref)
            supported &= assessment.status == ClaimSupportStatus.SUPPORTED
            values.extend(claim.value for claim in assessment.claims)
            claims.extend({"subject": claim.subject, "relation": claim.relation, "value": claim.value,
                           "evidence_refs": list(claim.supporting_evidence_refs)} for claim in assessment.claims)
    sources = [by_ref[ref]["relative_path"] for ref in final["evidence_refs"] if ref in by_ref] if final else []
    complete = bool(final and final["decision"]["value"])
    outcome = "complete" if complete else "partial" if values else "refused" if final else "runtime_failure"
    reason = (None if complete else "conflicting_evidence" if final and final["uncertainty"]["status"] == "conflicting_evidence"
              else "evidence_insufficient" if final else (status.error or {}).get("code", "runtime_failure"))
    allowed_paths = {doc["path"] for doc in case["documents"] if doc["workspace_id"] == case["workspace_id"]}
    sizes = [len(json.dumps(request, ensure_ascii=False).encode()) for request in observed.requests]
    result.update(outcome=outcome, reason=reason, values=values, sources=sources, final=final,
        run_status=status.status, error=status.error, reviewer=payload.get("review"),
        calls=call_rows, reads=reads, claims=claims, bindings_verified=bindings_valid,
        search_observations=engine.search_observations, claim_supported=bool(supported),
        citation_valid=not final or all(ref in by_ref for ref in final["evidence_refs"]),
        workspace_valid=all(row["relative_path"] in allowed_paths and row.get("workspace_id") == state.source.vault_root_fingerprint for row in reads),
        source_identity_valid=all(row.get("evidence_id") and row.get("source_ref") == row["relative_path"]
            and row.get("quote_sha256") == hashlib.sha256(row["quote"].encode("utf-8")).hexdigest() for row in reads),
        version_valid=all(sha(vault / row["relative_path"]) == row["content_sha256"] for row in reads),
        model_turns=len(observed.requests), context_bytes=max(sizes, default=0), context_total_bytes=sum(sizes),
        search_candidate_count=sum(row["candidate_count"] for row in engine.search_observations),
        verified_read_count=len(reads), elapsed_ms=round((time.monotonic() - started) * 1000),
        artifact_root=str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
        fault_applied=fault_applied)
    write(root / "requests.json", observed.requests)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = parse_candidate(json.loads(args.candidate.read_text(encoding="utf-8")))
    public = json.loads(args.public.read_text(encoding="utf-8"))
    rows = [execute(case, candidate) for case in public["cases"]]
    write(args.output, {"partition": public.get("partition"), "candidate": candidate.to_dict(), "cases": rows})


if __name__ == "__main__":
    main()

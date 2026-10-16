"""Offline, answer-blind execution for the qualification Core Gate.

The public entry point deliberately accepts only the trusted projection from
``evaluation_qualification.public_projection``.  Gold, target labels and
partition metadata never enter the Runtime request or the local provider.
"""
from __future__ import annotations

from collections import Counter
import json
import hashlib
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from linkloom.agents.claim_grounding import ClaimSupportStatus, assess_claim_text
from linkloom.agents.model_adapter import ModelAction, ModelProviderError, ModelResponse
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.runtime.adaptive_planning import validate_source_relation_plan
from linkloom.runtime.checkpoint import InMemoryCheckpointer, SQLiteCheckpointer
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.runtime.task_plan import (
    TaskPlan,
    assess_task_plan,
    verified_dependency_entities,
    verified_plan_conflict_refs,
    verified_plan_subgoal_values,
)
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


PUBLIC_KEYS = frozenset({"query", "workspace_id", "documents", "task_plan", "budget", "fault"})
FAULTS = frozenset({None, "invalid_tool", "invalid_final_once", "cold_resume",
                    "provider_uncertain", "wrong_workspace", "source_change",
                    "budget_exhausted", "unverified_dependency"})


class ProcessCrash(BaseException):
    """Test-only process death which leaves the last SQLite checkpoint durable."""


class CrashAfterResponse(SQLiteCheckpointer):
    def save(self, state, checkpoint_id=None):
        result = super().save(state, checkpoint_id)
        if state.model_executions:
            record = state.model_executions[-1]
            if record.status == "response_durable":
                action = record.normalized_action or {}
                if action.get("kind") == "tool_call":
                    raise ProcessCrash("cold_resume: response_durable")
        return result


class QualifiedRuntimeEngine(RuntimeEngine):
    """Runtime subclass used only to expose real executor counters."""

    def __init__(self, *args, **kwargs):
        self.executor_counts = Counter()
        super().__init__(*args, **kwargs)

    def _build_runtime_agent_adapter(self):
        """Expose real executor counts through production current retrieval."""
        adapter = super()._build_runtime_agent_adapter()
        # Count the backend boundary after semantic argument validation.
        original_search = adapter._retrieval_backend.search
        original_read = adapter._read_verified_note_for_tool

        def counted_search(*args, **kwargs):
            self.executor_counts["search_notes"] += 1
            return original_search(*args, **kwargs)

        def counted_read(*args, **kwargs):
            self.executor_counts["read_verified_note"] += 1
            return original_read(*args, **kwargs)

        adapter._retrieval_backend.search = counted_search
        adapter._read_verified_note_for_tool = counted_read
        return adapter


class SourceOnlyController:
    """Choose the next action only from Runtime requests and observations."""

    def __init__(self, *, fault: str | None, vault_root: Path):
        self.fault = fault
        self.vault_root = vault_root
        self.slot = 0
        self.pending: list[str] = []
        self.reads: dict[str, list[dict[str, Any]]] = {}
        self.values: dict[str, str] = {}
        self.observed_dependencies: dict[str, dict[str, str]] = {}
        self.claims: list[dict[str, Any]] = []
        self.searched = False
        self.invalid_tool_sent = False
        self.invalid_final_sent = False
        self.source_mutated = False
        self.source_mutation = None
        self.requests: list[Any] = []

    @staticmethod
    def _packet(request: Any) -> dict[str, Any]:
        marker = "team_decision_request="
        if marker not in request.user_input:
            raise ValidationError("qualified controller received an unexpected request envelope")
        return json.JSONDecoder().raw_decode(request.user_input.split(marker, 1)[1])[0]

    @staticmethod
    def _tool(request: Any, name: str, arguments: dict[str, Any]) -> ModelAction:
        return ModelAction.tool(ToolCall(
            call_id=f"qualified-{request.sequence}-{name}",
            tool_id=name,
            arguments=arguments,
        ))

    def _mutate_source_after_read(self, relative_path: str | None = None) -> None:
        if self.source_mutated or self.fault != "source_change":
            return
        if not relative_path:
            raise ValidationError("Freshness injection requires the actual verified-read source path")
        path = self.vault_root / relative_path
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text(path.read_text(encoding="utf-8") + "\nMutation after verified read.\n", encoding="utf-8")
        self.source_mutation = {"path": relative_path, "before_sha256": before,
                                "after_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.source_mutated = True

    def _final(self, plan: TaskPlan, evidence: dict[str, tuple[dict[str, Any], ...]]) -> ModelAction:
        complete = all(goal.subgoal_id in self.values for goal in plan.subgoals)
        current = plan.subgoals[self.slot]
        conflict = bool(verified_plan_conflict_refs(plan, current.subgoal_id, evidence)) if not complete else False
        missing = [item.subgoal_id for item in plan.subgoals if item.subgoal_id not in self.values]
        unknown_refs = (list(dict.fromkeys(item["evidence_id"] for item in evidence.get(current.subgoal_id, ())))
                        if not complete else [])
        claim_refs = list(dict.fromkeys(ref for claim in self.claims for ref in claim["evidence_refs"]))
        refs = list(dict.fromkeys(claim_refs + unknown_refs))
        payload = {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": "; ".join(item["point"] for item in self.claims) if complete else None,
                "status": "approved" if complete else "insufficient_evidence",
                "evidence_refs": claim_refs if complete else [],
            },
            "rationale": [] if complete else self.claims,
            "actions": [],
            "unresolved_items": [],
            "rejected_alternatives": [],
            "uncertainty": {
                "status": "none" if complete else "conflicting_evidence" if conflict else "insufficient_evidence",
                "statement": None,
                "unknown_fields": missing,
                "evidence_refs": unknown_refs,
            },
            "evidence_refs": refs,
        }
        return ModelAction.final(json.dumps(payload, ensure_ascii=False))

    def __call__(self, request: Any) -> ModelAction:
        self.requests.append(request)
        packet = self._packet(request)
        plan = TaskPlan.from_dict(packet["task_plan"])
        if self.invalid_final_sent and request.available_tools == []:
            evidence = {row.subgoal_id: tuple(self.reads.get(row.subgoal_id, ())) for row in plan.subgoals}
            return self._final(plan, evidence)
        if self.slot >= len(plan.subgoals):
            raise ValidationError("qualified controller advanced beyond the TaskPlan")
        goal = plan.subgoals[self.slot]
        self.reads.setdefault(goal.subgoal_id, [])
        observation = request.observation
        if observation is not None:
            if observation.status != "ok":
                raise ValidationError("qualified controller stops on observed tool failure")
            if observation.tool_id == "search_notes":
                values = observation.value if isinstance(observation.value, list) else []
                self.pending = [item["evidence_id"] for item in values]
            elif observation.tool_id == "read_verified_note" and isinstance(observation.value, dict):
                self.reads[goal.subgoal_id].append(observation.value)
                self._mutate_source_after_read(observation.value.get("relative_path"))
                if self.fault == "source_change" and self.source_mutated:
                    # Re-read the exact evidence that was observed before the
                    # mutation.  The production read validator must reject
                    # this stale identity before another executor call.
                    self.pending.insert(0, observation.value["evidence_id"])

        def search_args(subgoal, *, bad_identity: bool = False) -> dict[str, Any]:
            dependencies = {parent: self.values[parent] for parent in subgoal.depends_on}
            if self.fault == "unverified_dependency" and dependencies:
                dependencies = {parent: "Forged Owner" for parent in dependencies}
            self.observed_dependencies[subgoal.subgoal_id] = dict(dependencies)
            source_context = {
                **packet["source_context"],
                "subgoal_id": subgoal.subgoal_id,
                "dependency_entities": dependencies,
                "query_variant": "primary",
            }
            if bad_identity:
                source_context["vault_root_fingerprint"] = "wrong-workspace"
            return {"query": subgoal.question, "source_context": source_context,
                    "limit": 0 if self.fault == "invalid_tool" and not self.invalid_tool_sent else 4}

        if self.fault == "invalid_tool" and not self.invalid_tool_sent:
            invalid_args = search_args(goal)
            self.invalid_tool_sent = True
            return self._tool(request, "search_notes", invalid_args)
        if self.fault == "wrong_workspace" and not self.searched:
            self.searched = True
            return self._tool(request, "search_notes", search_args(goal, bad_identity=True))
        if self.pending:
            return self._tool(request, "read_verified_note", {"note_ref": self.pending.pop(0)})
        if not self.searched:
            self.searched = True
            return self._tool(request, "search_notes", search_args(goal))

        evidence = {row.subgoal_id: tuple(self.reads.get(row.subgoal_id, [])) for row in plan.subgoals}
        targets = verified_plan_subgoal_values(plan, goal.subgoal_id, evidence)
        if len(targets or ()) == 1:
            target = targets[0]
            match = next((re.search(re.escape(target), item["quote"], re.I)
                          for item in evidence[goal.subgoal_id]
                          if re.search(re.escape(target), item["quote"], re.I)), None)
            if match is not None:
                self.values[goal.subgoal_id] = match.group()
            literal = self.values.get(goal.subgoal_id, target)
            subject = (self.values[goal.depends_on[0]] if goal.depends_on else
                       re.search(r"(?:Who owns |Which project does )(.+?)(?: alias)?\?$", goal.question)[1])
            predicate = goal.relation_path[0]
            text = {
                "alias_of_project": f"{subject} is the team alias for {literal}",
                "owner_of_project": f"{literal} is the accountable owner of {subject}",
                "reports_to": f"{subject} reports directly to {literal}",
            }[predicate]
            by_ref = {item["evidence_id"]: item for item in evidence[goal.subgoal_id]}
            assessment = assess_claim_text(text, list(by_ref), by_ref)
            if assessment.status != ClaimSupportStatus.SUPPORTED:
                raise ValidationError("observed source binding does not ground a claim")
            refs = list(dict.fromkeys(ref for claim in assessment.claims
                                     for ref in claim.supporting_evidence_refs))
            claim = {"point": text, "evidence_refs": refs}
            if claim not in self.claims:
                self.claims.append(claim)
            if self.slot + 1 < len(plan.subgoals):
                self.slot += 1
                self.searched = True
                return self._tool(request, "search_notes", search_args(plan.subgoals[self.slot]))

        if self.fault == "invalid_final_once" and not self.invalid_final_sent:
            self.invalid_final_sent = True
            return ModelAction.final('{"schema_version":')
        return self._final(plan, evidence)


class QualifiedProvider:
    """Provider-neutral adapter that never contacts an SDK or network."""

    def __init__(self, controller: SourceOnlyController, *, fault: str | None):
        self.controller = controller
        self.fault = fault
        self.requests: list[Any] = []
        self.tool_errors: list[dict[str, Any]] = []

    def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        if request.observation is not None and request.observation.status != "ok":
            error = request.observation.error
            self.tool_errors.append({
                "tool": request.observation.tool_id,
                "code": error.get("code") if isinstance(error, dict) else None,
                "message": error.get("message") if isinstance(error, dict) else str(error),
            })
        if self.fault == "provider_uncertain" and len(self.requests) == 1:
            return ModelResponse(error=ModelProviderError(
                code="MODEL_UNKNOWN_FAILURE", category="unknown_provider_error",
                message="Synthetic provider outcome is uncertain.",
                outcome="unknown_provider_outcome",
            ))
        return ModelResponse(
            action=self.controller(request),
            provider_metadata={"adapter": "qualified-runtime-v1", "network": "deny"},
        )


def _validate_public(public: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], dict[str, Any], TaskPlan, str | None]:
    if not isinstance(public, dict):
        raise TypeError("qualified runtime input must be an object")
    extra = set(public) - PUBLIC_KEYS
    if extra:
        raise ValueError(f"qualified runtime public projection contains forbidden keys: {sorted(extra)}")
    query = public.get("query", "")
    workspace = public.get("workspace_id", "")
    documents = public.get("documents", [])
    budget = public.get("budget", {})
    fault = public.get("fault")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("qualified runtime query is required")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("qualified runtime workspace_id is required")
    if not isinstance(documents, list) or not isinstance(budget, dict):
        raise TypeError("qualified runtime documents and budget must be arrays/object")
    if fault not in FAULTS:
        raise ValueError(f"unsupported qualified runtime fault: {fault!r}")
    if not isinstance(public.get("task_plan"), dict):
        raise ValueError("qualified runtime task_plan is required")
    if public["task_plan"].get("goal") != query.strip():
        raise ValueError("task plan goal/query mismatch")
    max_steps = budget.get("max_steps")
    max_provider_requests = budget.get("max_provider_requests")
    if (isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1
            or isinstance(max_provider_requests, bool) or not isinstance(max_provider_requests, int)
            or max_provider_requests < 1):
        raise ValueError("qualified runtime budgets must be positive integers")
    plan = validate_source_relation_plan(public["task_plan"], user_query=query, max_steps=max_steps)
    return query, workspace, documents, budget, plan, fault


def _write_fixture_documents(documents: list[dict[str, Any]], workspace: str, vault: Path) -> list[str]:
    foreign: list[str] = []
    for document in documents:
        if not isinstance(document, dict):
            raise TypeError("document must be an object")
        path_text = document.get("path", "")
        path = Path(path_text)
        if (not path_text or path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md"):
            raise ValueError(f"invalid fixture note path: {path_text!r}")
        if document.get("workspace_id") != workspace:
            foreign.append(path.as_posix())
            continue
        target = vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(document.get("content", "")), encoding="utf-8")
    return foreign


def _result_payload(engine: RuntimeEngine, status: Any) -> dict[str, Any]:
    path = engine.checkpoint_dir / "results" / status.run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _observe_result(state: Any, payload: dict[str, Any], plan: TaskPlan, *, engines: list[QualifiedRuntimeEngine],
                   provider_requests: int, providers: list[QualifiedProvider],
                   controller: SourceOnlyController, public: dict[str, Any],
                   foreign_documents: list[str]) -> dict[str, Any]:
    records = list(state.tool_ledger) if state is not None else []
    calls: list[dict[str, Any]] = []
    binding_ok = True
    searches = 0
    reads: dict[str, str] = {}
    for record in records:
        row = {"sequence": record.sequence, "tool": record.tool_id, "status": record.status}
        if record.tool_id == "search_notes":
            searches += 1
            context = record.arguments.get("source_context", {})
            row["hop"] = context.get("subgoal_id")
            row["dependencies"] = context.get("dependency_entities", {})
            prior = [item for item in records if item.sequence < record.sequence]
            try:
                assessment = assess_task_plan(
                    plan, prior, run_id=record.run_id, task_id=record.task_id,
                    agent_id=record.agent_id,
                    expected_workspace_id=state.source.vault_root_fingerprint,
                )
                verified_dependency_entities(plan, assessment, row["hop"], row["dependencies"])
            except (ValidationError, KeyError, TypeError):
                binding_ok = False
        if record.tool_id == "read_verified_note" and record.status == "completed" and record.result:
            value = record.result["value"]
            row.update(hop=None, source=value.get("relative_path"), evidence_id=value.get("evidence_id"),
                       content_sha256=value.get("content_sha256"))
            if value.get("evidence_id"):
                reads[value["evidence_id"]] = value.get("relative_path", "")
        calls.append(row)

    final = (payload.get("result") or {}).get("team_decision")
    final_refs = list(final.get("evidence_refs", [])) if isinstance(final, dict) else []
    read_values = {}
    for record in records:
        if record.tool_id == "read_verified_note" and record.status == "completed" and record.result:
            value = record.result.get("value", {})
            if value.get("evidence_id"):
                read_values[value["evidence_id"]] = value
    values: list[str] = []
    if final:
        texts = ([final["decision"]["value"]] if final.get("decision", {}).get("value")
                 else [item.get("point", "") for item in final.get("rationale", [])])
        for text in texts:
            support = assess_claim_text(text, final_refs, read_values)
            values.extend(claim.value for claim in support.claims)
    citation_valid = bool(final and final_refs and all(ref in reads for ref in final_refs))
    outcome = "complete" if final and final.get("decision", {}).get("value") else "partial" if values else "refused"
    fault = public.get("fault")
    if state is None or state.status != "completed":
        outcome = "refused"
    response_origins = sorted({record.response_origin for record in getattr(state, "model_executions", [])
                               if record.response_origin}) if state else []
    model_request_sentinel_hits = sum(
        "__gold_sentinel__" in json.dumps(request.to_dict(), ensure_ascii=False)
        for provider in providers for request in provider.requests
    )
    tool_errors = [error for provider in providers for error in provider.tool_errors]
    result: dict[str, Any] = {
        "schema_version": "qualified-runtime/v1",
        "run_status": state.status if state else "failed",
        "outcome": outcome,
        "reason": (final.get("uncertainty", {}).get("status") if final and not final.get("decision", {}).get("value")
                    else None),
        "values": values,
        "calls": calls,
        "sources": sorted(set(reads.values())),
        "foreign_documents_skipped": sorted(foreign_documents),
        "citation_valid": citation_valid,
        "bindings_verified": bool(searches) and binding_ok,
        "observed_bindings": dict(controller.observed_dependencies),
        "verified_values": dict(controller.values),
        "executor_search_calls": sum(engine.executor_counts["search_notes"] for engine in engines),
        "executor_read_calls": sum(engine.executor_counts["read_verified_note"] for engine in engines),
        "verified_hops": len(values),
        "model_turns": len(getattr(state, "model_executions", [])) if state else provider_requests,
        "provider_requests": provider_requests,
        "real_provider_requests": 0,
        "model_executions": [record.to_dict() for record in getattr(state, "model_executions", [])],
        "response_origins": response_origins,
        "model_request_sentinel_hits": model_request_sentinel_hits,
        # Kept as a derived diagnostic for older report consumers.  Tests use
        # the concrete sentinel-hit count above rather than this boolean.
        "model_gold_access": model_request_sentinel_hits > 0,
        "tool_errors": tool_errors,
        "source_mutated": controller.source_mutated,
        "source_mutation": controller.source_mutation,
        "termination": state.termination.to_dict() if state and state.termination else None,
        "run_id": state.run_id if state else None,
        "error": state.error.to_dict() if state and state.error else None,
        "final": final,
        "fault": fault,
        "source_identity": {
            "index_sha256": state.source.index_sha256 if state else None,
            "vault_root_fingerprint": state.source.vault_root_fingerprint if state else None,
            "document_count": state.source.document_count if state else 0,
        },
    }
    if fault == "invalid_final_once" and state:
        rejected = sum(record.status == "final_rejected" for record in state.model_executions)
        corrections = sum(record.status == "completed" and (record.normalized_action or {}).get("kind") == "final"
                           for record in state.model_executions)
        result["final_correction"] = {"invalid_response_count": rejected,
                                       "correction_response_count": corrections}
    return result


def execute_public(public: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Execute one trusted public projection through the production Runtime."""
    query, workspace, documents, budget, plan, fault = _validate_public(public)
    run_root = Path(root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    vault = run_root / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    foreign = _write_fixture_documents(documents, workspace, vault)
    scan = scan_vault(vault, run_root / "scan")
    controller = SourceOnlyController(fault=fault, vault_root=vault)
    provider = QualifiedProvider(controller, fault=fault)
    checkpointer = (CrashAfterResponse(run_root / "checkpoints") if fault == "cold_resume"
                    else SQLiteCheckpointer(run_root / "checkpoints"))
    first = QualifiedRuntimeEngine(
        vault_root=vault, index_path=scan.index_path, checkpoint_dir=run_root / "checkpoints",
        trace_dir=run_root / "traces", checkpointer=checkpointer, model=provider,
        memory_enabled=False, decision_memory_enabled=False,
    )
    engines = [first]
    providers = [provider]
    thread_id = "qualified-runtime-thread"
    request = RunRequest(
        request_id=f"qualified-{uuid4().hex[:12]}", thread_id=thread_id,
        workflow="team_decision", query=query, task_plan=plan.to_dict(),
        max_steps=budget["max_steps"], max_provider_requests=budget["max_provider_requests"],
        dry_run=True,
    )
    crashed = False
    try:
        status = first.start_multi_agent(request)
    except ProcessCrash:
        crashed = True
        resumed_provider = QualifiedProvider(
            SourceOnlyController(fault=None, vault_root=vault), fault=None,
        )
        providers.append(resumed_provider)
        resumed = QualifiedRuntimeEngine(
            vault_root=vault, index_path=scan.index_path, checkpoint_dir=run_root / "checkpoints",
            trace_dir=run_root / "traces", checkpointer=SQLiteCheckpointer(run_root / "checkpoints"),
            model=resumed_provider, memory_enabled=False, decision_memory_enabled=False,
        )
        engines.append(resumed)
        status = resumed.resume_multi_agent(thread_id)
        provider_requests = len(provider.requests) + len(resumed_provider.requests)
        controller = resumed_provider.controller
    else:
        provider_requests = len(provider.requests)
    state = engines[-1].checkpointer.get_latest(thread_id)
    payload = _result_payload(engines[-1], status)
    result = _observe_result(state, payload, plan, engines=engines, provider_requests=provider_requests,
                             providers=providers, controller=controller, public=public,
                             foreign_documents=foreign)
    if fault == "cold_resume":
        search_records = {
            record.call_id for record in getattr(state, "tool_ledger", [])
            if record.tool_id == "search_notes" and record.status == "completed"
        }
        result["cold_resume"] = {
            "crashed_after_durable_response": crashed,
            "new_provider_requests": max(0, provider_requests - len(provider.requests)),
            "response_origin": "provider" if result["response_origins"] == ["provider"] else result["response_origins"],
            "duplicate_executor_calls": max(0, result["executor_search_calls"] - len(search_records)),
        }
    (run_root / "observation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result



if __name__ == "__main__":
    raise SystemExit("Use scripts/evaluation_core_gate.py; validation Final execution is unavailable.")

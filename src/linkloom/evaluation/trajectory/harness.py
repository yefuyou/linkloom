"""Synthetic-only adapter that drives the production tool runtime contracts."""

from __future__ import annotations

from collections import defaultdict
import importlib
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

import linkloom


def _bootstrap_runtime_foundations() -> types.ModuleType | None:
    """Load the runtime leaves without triggering the package import cycle."""

    if "linkloom.runtime" in sys.modules:
        return None
    runtime_root = Path(__file__).resolve().parents[2] / "runtime"
    package = types.ModuleType("linkloom.runtime")
    package.__path__ = [str(runtime_root)]
    package.__package__ = "linkloom.runtime"
    sys.modules["linkloom.runtime"] = package
    setattr(linkloom, "runtime", package)
    for module_name in ("errors", "models"):
        qualified_name = f"linkloom.runtime.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified_name,
            runtime_root / f"{module_name}.py",
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {qualified_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)
    return package


# The current production package initializers form a contracts/recovery import
# cycle. Bootstrap only their leaf modules, import the canonical tool contracts,
# then restore the normal runtime package. No production module is modified.
_RUNTIME_BOOTSTRAP = _bootstrap_runtime_foundations()

from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.recovery import decide_pending_recovery
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.tool_policy import (
    P4_TOOL_POLICY_VERSION,
    ToolCallPolicy,
    ToolPolicyEnforcer,
)

if _RUNTIME_BOOTSTRAP is not None:
    sys.modules.pop("linkloom.runtime", None)
    if getattr(linkloom, "runtime", None) is _RUNTIME_BOOTSTRAP:
        delattr(linkloom, "runtime")
    importlib.import_module("linkloom.runtime")

from .capabilities import assess_case_capabilities
from .fixture import TrajectoryFixture
from .models import (
    ATTRIBUTION_SIGNAL_PRIORITY,
    OBSERVATION_SCHEMA_VERSION,
    TrajectoryCase,
    TrajectoryObservation,
)


_DENIED_TOOL_IDS = ("write_file", "read_gold", "raw_filesystem")
_ANCHORS = {
    "n/agent-loop.md": "agent-loop-control",
    "n/checkpoint.md": "checkpoint-boundary",
    "n/empty.md": "empty",
    "n/memory-policy.md": "memory-policy",
    "n/missing.md": "missing-note-sentinel",
    "n/retrieval.md": "retrieval-contract",
}


class TrajectoryHarness:
    """Execute supported cases without a model, provider, network, or real vault."""

    def __init__(
        self,
        repo_root: str | Path,
        fixture_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        expected_fixture = (
            self.repo_root / "tests" / "fixtures" / "trajectory_kb_v1"
        ).resolve()
        if fixture_root is not None and Path(fixture_root).resolve() != expected_fixture:
            raise ValueError("fixture override must resolve to synthetic trajectory_kb_v1")
        self.fixture = TrajectoryFixture(self.repo_root)
        self.executor_counts: dict[str, int] = defaultdict(int)
        self.denied_side_effect_executor_counts = {
            tool_id: 0 for tool_id in _DENIED_TOOL_IDS
        }
        self.executed_case_ids: list[str] = []

    def execute_case(self, case: TrajectoryCase) -> TrajectoryObservation:
        support = assess_case_capabilities(case)
        if not support.executable:
            raise ValueError("future capability cases must not be executed")
        self.executed_case_ids.append(case.case_id)
        if case.case_id in {"CP-03", "CP-04"}:
            return self._observe_pending_recovery(case)
        return self._execute_runtime_case(case)

    def _execute_runtime_case(self, case: TrajectoryCase) -> TrajectoryObservation:
        ledger = ToolExecutionLedger()
        executor_invocations: list[dict[str, str]] = []
        suppressed_call_ids: list[str] = []
        denied_accesses: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        trajectory: list[dict[str, Any]] = []
        histories: dict[str, list[str]] = {}
        active_call: list[ToolCall | None] = [None]
        injection_kind = case.fault_injection["kind"]

        def record_invocation(tool_id: str) -> ToolCall:
            call = active_call[0]
            if call is None:
                raise RuntimeError("synthetic executor has no active ToolCall")
            executor_invocations.append({"call_id": call.call_id, "tool_id": tool_id})
            self.executor_counts[tool_id] += 1
            return call

        def search_executor(
            query: str,
            source_context: dict[str, Any],
            limit: int,
        ) -> Any:
            call = record_invocation("search_notes")
            if injection_kind == "executor_exception":
                raise RuntimeError("synthetic search failure")
            if case.case_id == "OUT-01":
                raw_output = dict(case.fault_injection["raw_output"])
                trajectory.append(
                    {
                        "event": "output_validation_failed",
                        "expected_shape": "array",
                        "actual_shape": "object",
                    }
                )
                return raw_output
            if case.case_id == "OUT-03":
                raw_result = ToolResult(
                    call_id=case.fault_injection["returned_call_id"],
                    tool_id=case.fault_injection["returned_tool_id"],
                    status="ok",
                    value=[],
                    business_status="FOUND",
                )
                if (
                    raw_result.call_id != call.call_id
                    or raw_result.tool_id != call.tool_id
                ):
                    trajectory.append(
                        {
                            "event": "output_identity_mismatch",
                            "expected_call_id": call.call_id,
                            "actual_call_id": raw_result.call_id,
                        }
                    )
                return raw_result
            matches = self._search_fixture(query, limit)
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="ok",
                value=matches,
                business_status="FOUND" if matches else "NOT_FOUND",
            )

        def read_executor(note_ref: str) -> Any:
            call = record_invocation("read_verified_note")
            if case.case_id == "PART-02" and note_ref == case.fault_injection["failed_path"]:
                raise RuntimeError("synthetic note read failure")
            if case.case_id == "OUT-02":
                raw_output = list(case.fault_injection["raw_output"])
                trajectory.append(
                    {
                        "event": "output_validation_failed",
                        "expected_shape": "object",
                        "actual_shape": "array",
                    }
                )
                return raw_output
            if case.case_id == "PART-03" and note_ref == case.fault_injection["invalid_path"]:
                raw_output = [self._safe_note_value(note_ref)]
                trajectory.append(
                    {
                        "event": "output_validation_failed",
                        "expected_shape": "object",
                        "actual_shape": "array",
                    }
                )
                return raw_output
            self.fixture.resolve(note_ref)
            if note_ref == "n/missing.md":
                return ToolResult(
                    call_id=call.call_id,
                    tool_id=call.tool_id,
                    status="ok",
                    value={},
                    business_status="NOT_FOUND",
                )
            self.fixture.read_text(note_ref)
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="ok",
                value=self._safe_note_value(note_ref),
                business_status="FOUND",
            )

        def checkpoint_callback(active_ledger: ToolExecutionLedger) -> None:
            call = active_call[0]
            if call is None:
                raise RuntimeError("checkpoint has no active ToolCall")
            record = active_ledger.get(call.call_id)
            if record is None:
                raise RuntimeError("checkpoint has no ledger record")
            states = histories.setdefault(call.call_id, [])
            if not states or states[-1] != record.status:
                states.append(record.status)
            if injection_kind == "pending_checkpoint_failure" and record.status == "pending":
                raise RuntimeError("synthetic pending checkpoint failure")
            if injection_kind == "terminal_checkpoint_failure" and record.status != "pending":
                raise RuntimeError("synthetic terminal checkpoint failure")
            trajectory.append(
                {"event": "checkpoint_saved", "record_status": record.status}
            )

        runtime = create_retrieval_tool_runtime(
            search_executor,
            read_executor,
            ledger=ledger,
            checkpoint_callback=checkpoint_callback,
        )
        for tool_id in _DENIED_TOOL_IDS:
            runtime.registry.register(
                ToolDefinition(
                    tool_id=tool_id,
                    version="synthetic-v1",
                    description="Synthetic denied side-effect sentinel.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                ),
                self._denied_executor(tool_id, active_call, executor_invocations),
            )

        policy = ToolCallPolicy(
            policy_version=P4_TOOL_POLICY_VERSION,
            agent_id="trajectory-harness",
            allowed_tool_ids=list(case.policy_budget["allowed_tools"]),
            denied_tool_ids=list(case.policy_budget["denied_tools"]),
            max_calls=case.policy_budget["max_tool_calls"],
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
        enforcer = ToolPolicyEnforcer(policy)

        for scripted in case.expected_tool_calls:
            call = ToolCall(
                call_id=scripted["call_id"],
                tool_id=scripted["tool_id"],
                arguments=dict(scripted["arguments"]),
                run_id=f"trajectory-{case.case_id.lower()}",
                task_id=case.case_id,
                agent_id="trajectory-harness",
                sequence=scripted.get("sequence", 0),
            )
            active_call[0] = call
            before_invocations = len(executor_invocations)
            trajectory.append(
                {"event": "tool_requested", "call_id": call.call_id, "tool_id": call.tool_id}
            )
            result = runtime.execute(call, enforcer)
            tool_calls.append(self._normalize_tool_call(call))
            tool_results.append(self._normalize_tool_result(result))
            error_code = result.error.code if result.error is not None else None
            trajectory.append(
                {
                    "event": "tool_result",
                    "call_id": call.call_id,
                    "status": result.status,
                    "error_code": error_code,
                }
            )
            if len(executor_invocations) == before_invocations:
                suppressed_call_ids.append(call.call_id)
            if error_code == "TOOL_PERMISSION_DENIED":
                denied_accesses.append(call.tool_id)
            if case.case_id == "ARG-03" and error_code == "TOOL_INVALID_ARGUMENTS":
                denied_accesses.append("unsafe_note_ref")

        evidence_refs = self._collect_evidence_refs(tool_results)
        error_codes = [
            result["error"]["code"] if isinstance(result.get("error"), dict) else None
            for result in tool_results
        ]
        signals = {
            "repeated_call_detected": "TOOL_LEDGER_CONFLICT" in error_codes,
        }
        history_payload = [
            {"call_id": call_id, "states": states}
            for call_id, states in histories.items()
        ]
        termination = self._derive_termination(case, tool_results, evidence_refs)
        attribution = self._derive_attribution(
            tool_results=tool_results,
            termination=termination,
            recovery_decisions=[],
            trajectory=trajectory,
        )
        return TrajectoryObservation(
            schema_version=OBSERVATION_SCHEMA_VERSION,
            case_id=case.case_id,
            trajectory=trajectory,
            tool_calls=tool_calls,
            tool_results=tool_results,
            termination=termination,
            attribution=attribution,
            execution={
                "executor_invocations": executor_invocations,
                "suppressed_call_ids": suppressed_call_ids,
                "budget": {
                    "used": enforcer.call_count,
                    "remaining": enforcer.remaining_calls,
                },
                "ledger": {"history": history_payload},
                "evidence_refs": evidence_refs,
                "denied_accesses": denied_accesses,
                "recovery_decisions": [],
                "signals": signals,
            },
            metadata={"run_id": f"trajectory-{case.case_id.lower()}"},
        )

    def _observe_pending_recovery(self, case: TrajectoryCase) -> TrajectoryObservation:
        scripted = case.expected_tool_calls[0]
        call = ToolCall(
            call_id=scripted["call_id"],
            tool_id=scripted["tool_id"],
            arguments=dict(scripted["arguments"]),
            sequence=scripted.get("sequence", 0),
        )
        ledger = ToolExecutionLedger()
        record = ledger.record_pending(call)
        decision = decide_pending_recovery(record)
        trajectory = [
            {"event": "checkpoint_loaded", "record_status": record.status},
            {"event": "recovery_recommendation", "decision": decision.decision},
        ]
        termination = {
            "status": "recovery_decision",
            "reason": (
                "pending_read"
                if decision.decision == "safe_to_retry"
                else "unknown_side_effect"
            ),
            "recommendation": decision.decision,
        }
        recovery_decisions = [decision.to_dict()]
        return TrajectoryObservation(
            schema_version=OBSERVATION_SCHEMA_VERSION,
            case_id=case.case_id,
            trajectory=trajectory,
            tool_calls=[self._normalize_tool_call(call)],
            tool_results=[],
            termination=termination,
            attribution=self._derive_attribution(
                tool_results=[],
                termination=termination,
                recovery_decisions=recovery_decisions,
                trajectory=trajectory,
            ),
            execution={
                "executor_invocations": [],
                "suppressed_call_ids": [call.call_id],
                "budget": {
                    "used": 0,
                    "remaining": case.policy_budget["max_tool_calls"],
                },
                "ledger": {
                    "history": [{"call_id": call.call_id, "states": [record.status]}]
                },
                "evidence_refs": [],
                "denied_accesses": [],
                "recovery_decisions": recovery_decisions,
                "signals": {},
            },
            metadata={"run_id": f"trajectory-{case.case_id.lower()}"},
        )

    def _denied_executor(
        self,
        tool_id: str,
        active_call: list[ToolCall | None],
        invocations: list[dict[str, str]],
    ):
        def executor(arguments: dict[str, Any]) -> dict[str, Any]:
            call = active_call[0]
            if call is None:
                raise RuntimeError("denied executor has no active call")
            self.denied_side_effect_executor_counts[tool_id] += 1
            self.executor_counts[tool_id] += 1
            invocations.append({"call_id": call.call_id, "tool_id": tool_id})
            return {"executed": True}

        return executor

    def _search_fixture(self, query: str, limit: int) -> list[dict[str, str]]:
        terms = [term for term in query.casefold().split() if term]
        matches: list[dict[str, str]] = []
        for note_ref in self.fixture.list_paths():
            if note_ref in {"n/empty.md", "n/missing.md"}:
                continue
            text = self.fixture.read_text(note_ref).casefold()
            if terms and all(term in text for term in terms):
                matches.append(self._safe_note_value(note_ref))
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _safe_note_value(note_ref: str) -> dict[str, str]:
        return {
            "note_path": note_ref,
            "evidence_ref": f"{note_ref}#{_ANCHORS[note_ref]}",
        }

    @staticmethod
    def _collect_evidence_refs(results: list[dict[str, Any]]) -> list[str]:
        refs: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                ref = value.get("evidence_ref")
                if isinstance(ref, str) and ref not in refs:
                    refs.append(ref)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        for result in results:
            visit(result.get("value"))
        return refs

    @staticmethod
    def _normalize_tool_call(call: ToolCall) -> dict[str, Any]:
        return {
            "call_id": call.call_id,
            "tool_id": call.tool_id,
            "arguments": dict(call.arguments),
            "sequence": call.sequence,
        }

    @staticmethod
    def _normalize_tool_result(result: ToolResult) -> dict[str, Any]:
        error = None
        if result.error is not None:
            error = {
                "code": result.error.code,
                "category": result.error.category,
                "message": result.error.message,
                "retryable": result.error.retryable,
                "safe_to_expose": result.error.safe_to_expose,
            }
        return {
            "call_id": result.call_id,
            "tool_id": result.tool_id,
            "status": result.status,
            "value": result.value,
            "business_status": result.business_status,
            "error": error,
        }

    @staticmethod
    def _derive_attribution(
        *,
        tool_results: list[dict[str, Any]],
        termination: dict[str, Any],
        recovery_decisions: list[dict[str, Any]],
        trajectory: list[dict[str, Any]],
    ) -> dict[str, str]:
        observable_signals = {
            f"termination.{termination['status']}",
            *(f"event.{event['event']}" for event in trajectory),
        }
        if recovery_decisions:
            observable_signals.add("recovery.present")
        for result in tool_results:
            error = result.get("error")
            if isinstance(error, dict):
                observable_signals.add(f"error.{error['code']}")
            business_status = result.get("business_status")
            if isinstance(business_status, str):
                observable_signals.add(f"business.{business_status}")
        for signal, (primary, stage) in ATTRIBUTION_SIGNAL_PRIORITY:
            if signal in observable_signals:
                return {"primary": primary, "stage": stage}
        raise ValueError(
            "supported trajectory produced no registered attribution signal"
        )

    @staticmethod
    def _derive_termination(
        case: TrajectoryCase,
        results: list[dict[str, Any]],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        codes = [
            result["error"]["code"] if isinstance(result.get("error"), dict) else None
            for result in results
        ]
        if case.category == "malformed_arguments":
            return {"status": "safe_error", "reason": "malformed_arguments"}
        if case.category == "permission_denied":
            reason = {
                "write_file": "permission_denied",
                "read_gold": "gold_access_denied",
                "raw_filesystem": "raw_filesystem_denied",
            }[case.expected_tool_calls[0]["tool_id"]]
            return {"status": "refused", "reason": reason}
        if case.category == "budget_exhausted":
            used = sum(result.get("status") == "ok" for result in results)
            return {
                "status": "budget_exhausted",
                "reason": "zero_budget" if used == 0 else "second_call_denied",
                "calls_used": used,
            }
        if case.category == "tool_execution_failure":
            if "TOOL_PENDING_CHECKPOINT_FAILED" in codes:
                return {
                    "status": "safe_error",
                    "reason": "pending_checkpoint_failure",
                    "ledger_status": "pending",
                }
            if "TOOL_TERMINAL_CHECKPOINT_FAILED" in codes:
                return {
                    "status": "durability_uncertain",
                    "reason": "terminal_checkpoint_failure",
                    "executor_completed": True,
                }
            return {"status": "safe_error", "reason": "tool_execution_failure"}
        if case.category == "invalid_output":
            return {
                "status": "safe_error",
                "reason": (
                    "result_identity_mismatch"
                    if case.fault_injection["kind"] == "mismatched_result_identity"
                    else "invalid_tool_output"
                ),
            }
        if case.category == "NOT_FOUND":
            return {"status": "completed", "reason": "not_found_reported"}
        if case.category == "partial_retrieval_failure":
            if "TOOL_EXECUTION_FAILED" in codes:
                reason = "one_branch_failed"
            elif "TOOL_INVALID_OUTPUT" in codes:
                reason = "one_branch_invalid"
            else:
                reason = "one_branch_not_found"
            return {
                "status": "partial",
                "reason": reason,
                "valid_evidence_preserved": bool(evidence_refs),
            }
        if case.category == "repeated_tool_call":
            return {"status": "safe_error", "reason": "ledger_conflict"}
        if case.case_id == "CP-01":
            return {"status": "completed", "reason": "pending_boundary_observed"}
        return {"status": "safe_error", "reason": "unclassified_runtime_outcome"}


__all__ = ["TrajectoryHarness"]

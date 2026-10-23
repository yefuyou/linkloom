"""Single-call runtime lifecycle for registered Linkloom tools."""

from __future__ import annotations

import re
import traceback
from typing import Any, Callable

from linkloom.decision_memory.tool import parse_decision_memory_as_of
from linkloom.runtime.errors import ValidationError, ToolArgumentValidationError
from linkloom.runtime.models import _assert_json_safe_primitive
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import CallBoundExecutor, LedgerBoundExecutor, ToolRegistry
from linkloom.tools.tool_policy import ToolPolicyEnforcer


_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
ToolCheckpointCallback = Callable[[ToolExecutionLedger], None]
ToolArgumentValidator = Callable[[dict[str, Any]], None]


def _json_enum_contains(value: Any, enum: list[Any]) -> bool:
    """Compare JSON enum values without Python's bool/int aliasing."""
    for candidate in enum:
        if isinstance(value, bool) or isinstance(candidate, bool):
            if type(value) is type(candidate) and value == candidate:
                return True
            continue
        if isinstance(value, (int, float)) and isinstance(candidate, (int, float)):
            if value == candidate:
                return True
            continue
        if type(value) is type(candidate) and value == candidate:
            return True
    return False


def _schema_failure(path: str) -> None:
    raise ValidationError(
        "Tool schema validation failed.",
        details={"reason": "schema_validation", "path": path},
    )


def _validate_json_schema(value: Any, schema: Any, path: str) -> None:
    """Validate the small JSON-schema subset needed by the tool contracts."""
    if not isinstance(schema, dict):
        _schema_failure(path)

    try:
        _assert_json_safe_primitive(value, path)
    except ValidationError:
        _schema_failure(path)

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list):
            _schema_failure(path)
        if not _json_enum_contains(value, enum):
            _schema_failure(path)

    schema_type = schema.get("type")
    if schema_type is None:
        return
    if isinstance(schema_type, list):
        # JSON Schema also permits unions such as array|object for tool
        # observations. Preserve the nullable Provider contracts while
        # validating every member against the ordinary single-type rules.
        if (not schema_type
                or any(not isinstance(item, str) or item not in _SCHEMA_TYPES
                       for item in schema_type)
                or len(set(schema_type)) != len(schema_type)):
            _schema_failure(path)
        for allowed_type in schema_type:
            try:
                _validate_json_schema(value, {**schema, "type": allowed_type}, path)
            except ValidationError:
                continue
            return
        _schema_failure(path)
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        _schema_failure(path)

    if schema_type == "null":
        if value is not None:
            _schema_failure(path)
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            _schema_failure(path)
        return
    if schema_type == "string":
        if not isinstance(value, str):
            _schema_failure(path)
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                _schema_failure(path)
            try:
                matches = re.search(pattern, value) is not None
            except re.error:
                _schema_failure(path)
            if not matches:
                _schema_failure(path)
        _validate_string_limits(value, schema, path)
        return
    if schema_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            _schema_failure(path)
        _validate_number_limits(value, schema, path)
        return
    if schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _schema_failure(path)
        _validate_number_limits(value, schema, path)
        return
    if schema_type == "array":
        if not isinstance(value, list):
            _schema_failure(path)
        _validate_collection_limits(value, schema, path)
        items = schema.get("items")
        if items is not None:
            for index, item in enumerate(value):
                _validate_json_schema(item, items, f"{path}[{index}]")
        return

    if not isinstance(value, dict):
        _schema_failure(path)
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        _schema_failure(path)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
        _schema_failure(path)
    for key in required:
        if key not in value:
            _schema_failure(f"{path}.{key}")
    additional_properties = schema.get("additionalProperties", True)
    if not isinstance(additional_properties, bool):
        _schema_failure(path)
    if additional_properties is False:
        for key in value:
            if key not in properties:
                _schema_failure(f"{path}.{key}")
    for key, property_schema in properties.items():
        if key in value:
            _validate_json_schema(value[key], property_schema, f"{path}.{key}")


def _validate_string_limits(value: str, schema: dict[str, Any], path: str) -> None:
    for key, comparison in (
        ("minLength", lambda actual, expected: actual < expected),
        ("maxLength", lambda actual, expected: actual > expected),
    ):
        if key not in schema:
            continue
        limit = schema[key]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            _schema_failure(path)
        if comparison(len(value), limit):
            _schema_failure(path)


def _validate_number_limits(value: int | float, schema: dict[str, Any], path: str) -> None:
    for key, comparison in (
        ("minimum", lambda actual, expected: actual < expected),
        ("maximum", lambda actual, expected: actual > expected),
    ):
        if key not in schema:
            continue
        limit = schema[key]
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            _schema_failure(path)
        if comparison(value, limit):
            _schema_failure(path)


def _validate_collection_limits(
    value: list[Any],
    schema: dict[str, Any],
    path: str,
) -> None:
    for key, comparison in (
        ("minItems", lambda actual, expected: actual < expected),
        ("maxItems", lambda actual, expected: actual > expected),
    ):
        if key not in schema:
            continue
        limit = schema[key]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            _schema_failure(path)
        if comparison(len(value), limit):
            _schema_failure(path)


def _emit_tool_event(
    event_sink: Any,
    event_type: str,
    status: str,
    call: ToolCall,
    error: ToolError | None = None,
) -> None:
    """Emit a redacted lifecycle event without affecting tool execution."""
    if event_sink is None or not hasattr(event_sink, "emit"):
        return
    payload: dict[str, Any] = {
        "attributes": {"tool": call.tool_id, "call_id": call.call_id},
    }
    if error is not None:
        payload["error"] = error.to_dict()
    try:
        event_sink.emit(event_type, actor="agent", status=status, **payload)
    except Exception:
        # Observability is best effort and must not change the tool contract.
        return


def _make_error_result(
    call: ToolCall,
    code: str,
    category: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    error = ToolError(
        code=code,
        category=category,
        message=message,
        retryable=False,
        details=details or {},
        safe_to_expose=True,
    )
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="error",
        error=error,
    )


def _error_result(
    call: ToolCall,
    code: str,
    category: str,
    message: str,
    details: dict[str, Any] | None = None,
    event_sink: Any = None,
    ledger: ToolExecutionLedger | None = None,
    record_ledger: bool = False,
) -> ToolResult:
    result = _make_error_result(call, code, category, message, details)
    if record_ledger and ledger is not None:
        # The runtime has already crossed the authorization boundary.  The
        # normal lifecycle always has a pending record here.  Keep the safe
        # ToolResult even if a defensive ledger invariant rejects the update;
        # the exception text must never escape the tool boundary.
        try:
            ledger.record_failed(call, result)
        except ValidationError:
            pass
    _emit_tool_event(event_sink, "tool.failed", "failed", call, result.error)
    return result


def _persist_terminal(
    call: ToolCall,
    result: ToolResult,
    ledger: ToolExecutionLedger,
    checkpoint_callback: ToolCheckpointCallback | None,
    event_sink: Any,
    *,
    executor_status: str,
    external_execution_may_have_happened: bool | str,
) -> ToolResult:
    """Persist a terminal ledger state and normalize callback failure safely."""
    if checkpoint_callback is not None:
        try:
            checkpoint_callback(ledger)
        except Exception:
            details: dict[str, Any] = {
                "tool_id": call.tool_id,
                "phase": "terminal_checkpoint",
                "executor_status": executor_status,
                "durability": "uncertain",
                "external_execution_may_have_happened": external_execution_may_have_happened,
            }
            if result.error is not None:
                details["executor_error_code"] = result.error.code
            uncertain = _make_error_result(
                call,
                "TOOL_TERMINAL_CHECKPOINT_FAILED",
                "runtime",
                "Tool execution reached a terminal state, but its checkpoint is uncertain.",
                details,
            )
            if result.status == 'ok':
                ledger.record_checkpoint_uncertain(call, uncertain.error)
            _emit_tool_event(event_sink, "tool.failed", "failed", call, uncertain.error)
            return uncertain

    if result.status == "ok":
        _emit_tool_event(event_sink, "tool.completed", "ok", call)
    else:
        _emit_tool_event(event_sink, "tool.failed", "failed", call, result.error)
    return result


def _record_failed_and_persist(
    call: ToolCall,
    code: str,
    category: str,
    message: str,
    details: dict[str, Any],
    ledger: ToolExecutionLedger,
    checkpoint_callback: ToolCheckpointCallback | None,
    event_sink: Any,
    *,
    executor_status: str,
    external_execution_may_have_happened: bool | str,
) -> ToolResult:
    """Record a post-authorization failure, then checkpoint its terminal state."""
    result = _make_error_result(call, code, category, message, details)
    try:
        ledger.record_failed(call, result)
    except ValidationError:
        # Preserve the existing safe fallback for an internal ledger conflict.
        return _error_result(
            call,
            "TOOL_LEDGER_UPDATE_FAILED",
            "runtime",
            "Tool execution result could not be recorded.",
            {"tool_id": call.tool_id},
            event_sink,
            ledger=ledger,
            record_ledger=True,
        )
    return _persist_terminal(
        call,
        result,
        ledger,
        checkpoint_callback,
        event_sink,
        executor_status=executor_status,
        external_execution_may_have_happened=external_execution_may_have_happened,
    )


class EvidenceReferenceError(ValueError):
    """A read attempted an evidence ID absent from this run's search results."""

    def __init__(
        self, message: str, *, available_evidence_ids: tuple[str, ...] = (),
        candidates_truncated: bool = False,
    ) -> None:
        super().__init__(message)
        self.available_evidence_ids = available_evidence_ids
        self.candidates_truncated = candidates_truncated


class ToolPreconditionError(ValueError):
    """A dependent search was proposed before its parent evidence was read."""


class ToolRuntime:
    """Execute exactly one ToolCall through a registry and policy enforcer."""

    def __init__(
        self,
        registry: ToolRegistry,
        ledger: ToolExecutionLedger | None = None,
        checkpoint_callback: ToolCheckpointCallback | None = None,
        argument_validators: dict[str, ToolArgumentValidator] | None = None,
        reuse_preflight: Callable[[], Any] | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise ValidationError("ToolRuntime registry must be a ToolRegistry.")
        if ledger is not None and not isinstance(ledger, ToolExecutionLedger):
            raise ValidationError("ToolRuntime ledger must be a ToolExecutionLedger or None.")
        if checkpoint_callback is not None and not callable(checkpoint_callback):
            raise ValidationError("ToolRuntime checkpoint_callback must be callable or None.")
        if argument_validators is not None and (
            not isinstance(argument_validators, dict)
            or any(
                not isinstance(tool_id, str) or not callable(validator)
                for tool_id, validator in argument_validators.items()
            )
        ):
            raise ValidationError("ToolRuntime argument_validators must map tool IDs to callables.")
        if reuse_preflight is not None and not callable(reuse_preflight):
            raise ValidationError("ToolRuntime reuse_preflight must be callable or None.")
        self.registry = registry
        self.ledger = ledger or ToolExecutionLedger()
        self.checkpoint_callback = checkpoint_callback
        self.argument_validators = dict(argument_validators or {})
        self.reuse_preflight = reuse_preflight

    def source_allows_reuse(self) -> bool:
        """Check source freshness before a cached search skips its executor."""
        if self.reuse_preflight is None:
            return True
        try:
            self.reuse_preflight()
        except Exception:
            # The ordinary tool path reports the source error using its
            # existing ledger and normalized error contract.
            return False
        return True

    def _validate_registered_arguments(self, call: ToolCall, definition: ToolDefinition) -> None:
        _validate_json_schema(call.arguments, definition.input_schema, "$")
        validator = self.argument_validators.get(call.tool_id)
        if validator is not None:
            validator(call.arguments)

    def accepts_input(self, call: ToolCall) -> bool:
        """Check current registration and arguments without authorizing or executing."""
        try:
            definition, _ = self.registry.resolve(call.tool_id)
            self._validate_registered_arguments(call, definition)
        except (ValidationError, ValueError):
            return False
        return True

    def execute(
        self,
        call: ToolCall,
        policy_enforcer: ToolPolicyEnforcer,
        event_sink: Any = None,
        ledger: ToolExecutionLedger | None = None,
        checkpoint_callback: ToolCheckpointCallback | None = None,
    ) -> ToolResult:
        """Resolve, validate, authorize, execute, and normalize one call."""
        if not isinstance(call, ToolCall):
            raise ValidationError("ToolRuntime call must be a ToolCall.")
        if not isinstance(policy_enforcer, ToolPolicyEnforcer):
            raise ValidationError("ToolRuntime policy_enforcer must be a ToolPolicyEnforcer.")
        active_ledger = ledger or self.ledger
        if not isinstance(active_ledger, ToolExecutionLedger):
            raise ValidationError("ToolRuntime ledger must be a ToolExecutionLedger.")
        active_checkpoint_callback = (
            self.checkpoint_callback if checkpoint_callback is None else checkpoint_callback
        )
        if active_checkpoint_callback is not None and not callable(active_checkpoint_callback):
            raise ValidationError("ToolRuntime checkpoint_callback must be callable or None.")

        _emit_tool_event(event_sink, "tool.called", "started", call)

        try:
            definition, executor = self.registry.resolve(call.tool_id)
        except ValidationError as error:
            if error.details.get("reason") == "unknown_tool":
                return _error_result(
                    call,
                    "TOOL_UNKNOWN",
                    "schema",
                    "Unknown tool.",
                    {"tool_id": call.tool_id},
                    event_sink,
                )
            return _error_result(
                call,
                "TOOL_INVALID_ARGUMENTS",
                "input",
                "Tool arguments are invalid.",
                {"tool_id": call.tool_id},
                event_sink,
            )

        try:
            self._validate_registered_arguments(call, definition)
        except (ValidationError, ValueError) as error:
            details = {"tool_id": call.tool_id}
            path = error.details.get("path") if isinstance(error, ValidationError) else None
            if isinstance(path, str):
                details["path"] = path
            details["reason"] = (
                error.details.get("reason", "schema_violation")
                if isinstance(error, ValidationError) else "semantic_mismatch"
            )
            return _error_result(
                call,
                "TOOL_INVALID_ARGUMENTS",
                "input",
                "Tool arguments are invalid.",
                details,
                event_sink,
            )

        try:
            # Permission and budget eligibility must not be consumed until
            # the pending ledger record has crossed the durable checkpoint.
            policy_enforcer.check_call_eligibility(call.tool_id)
        except ValidationError as error:
            if error.details.get("reason") == "budget_exhausted":
                return _error_result(
                    call,
                    "TOOL_BUDGET_EXCEEDED",
                    "budget",
                    "Tool call budget exhausted.",
                    {"tool_id": call.tool_id},
                    event_sink,
                )
            return _error_result(
                call,
                "TOOL_PERMISSION_DENIED",
                "permission",
                "Tool permission denied.",
                {"tool_id": call.tool_id},
                event_sink,
            )

        try:
            active_ledger.record_pending(call)
        except ValidationError as error:
            # The policy has only been checked, not consumed.  A duplicate or
            # mismatched ledger identity is an internal runtime contract
            # failure and must not overwrite the existing record.
            details = {"tool_id": call.tool_id}
            reason = error.details.get("reason")
            if isinstance(reason, str):
                details["reason"] = reason
            return _error_result(
                call,
                "TOOL_LEDGER_CONFLICT",
                "runtime",
                "Tool execution ledger rejected this call.",
                details,
                event_sink,
            )

        # The pending record must be durable before any trusted executor can
        # run.  If this callback fails, the local ledger remains pending and
        # recovery can inspect it; the executor is never invoked.
        if active_checkpoint_callback is not None:
            try:
                active_checkpoint_callback(active_ledger)
            except Exception:
                return _error_result(
                    call,
                    "TOOL_PENDING_CHECKPOINT_FAILED",
                    "runtime",
                    "Tool execution could not establish a durable pending checkpoint.",
                    {
                        "tool_id": call.tool_id,
                        "phase": "pending_checkpoint",
                        "executor_started": False,
                        "durability": "uncertain",
                    },
                    event_sink,
                )

        try:
            # This is the single policy consumption point.  It is deliberately
            # after pending durability and before the executor.
            policy_enforcer.commit_call(call.tool_id)
        except ValidationError as error:
            reason = error.details.get("reason")
            if reason == "budget_exhausted":
                code = "TOOL_BUDGET_EXCEEDED"
                category = "budget"
                message = "Tool call budget exhausted."
            elif reason in {"core_denial", "explicit_denial", "not_allowed"}:
                code = "TOOL_PERMISSION_DENIED"
                category = "permission"
                message = "Tool permission denied."
            else:
                code = "TOOL_POLICY_COMMIT_FAILED"
                category = "runtime"
                message = "Tool policy consumption could not be committed."
            return _error_result(
                call,
                code,
                category,
                message,
                {
                    "tool_id": call.tool_id,
                    "phase": "policy_commit",
                    "executor_started": False,
                    "pending_record": "durable_or_local",
                },
                event_sink,
            )

        try:
            if isinstance(executor, LedgerBoundExecutor):
                raw_result = executor.execute_call(call, active_ledger.to_list())
            elif isinstance(executor, CallBoundExecutor):
                raw_result = executor.execute_call(call)
            else:
                raw_result = executor(call.arguments)
        except ToolArgumentValidationError as error:
            return _record_failed_and_persist(
                call, "TOOL_INVALID_ARGUMENTS", "input",
                "Tool arguments do not match the verified TaskPlan boundary.",
                {
                    "tool_id": call.tool_id,
                    "reason": error.details.get("reason", "task_plan_semantic_mismatch"),
                    "path": error.details.get("path", "$.source_context"),
                },
                active_ledger, active_checkpoint_callback, event_sink,
                executor_status="failed", external_execution_may_have_happened=False,
            )
        except ToolPreconditionError:
            return _record_failed_and_persist(
                call, "TOOL_PRECONDITION_FAILED", "input",
                "Read a returned parent evidence_id before the dependent search.",
                {"tool_id": call.tool_id, "reason": "missing_verified_parent_read"},
                active_ledger, active_checkpoint_callback, event_sink,
                executor_status="failed", external_execution_may_have_happened=False,
            )
        except EvidenceReferenceError as error:
            return _record_failed_and_persist(
                call, "TOOL_EVIDENCE_NOT_FOUND", "input",
                "Evidence ID was not returned by this run's search.",
                {
                    "tool_id": call.tool_id, "reason": "missing_evidence",
                    "available_evidence_ids": list(error.available_evidence_ids),
                    "candidates_truncated": error.candidates_truncated,
                },
                active_ledger, active_checkpoint_callback, event_sink,
                executor_status="failed", external_execution_may_have_happened=False,
            )
        except Exception as error:
            return _record_failed_and_persist(
                call,
                "TOOL_EXECUTION_FAILED",
                "runtime",
                "Tool execution failed.",
                {
                    "tool_id": call.tool_id,
                    "failure_diagnostic": _safe_exception_diagnostic(error),
                },
                active_ledger,
                active_checkpoint_callback,
                event_sink,
                executor_status="failed",
                external_execution_may_have_happened="unknown",
            )

        business_status: str | None = None
        output = raw_result
        if isinstance(raw_result, ToolResult):
            if (
                raw_result.call_id != call.call_id
                or raw_result.tool_id != call.tool_id
                or raw_result.status != "ok"
                or raw_result.error is not None
            ):
                return _record_failed_and_persist(
                    call,
                    "TOOL_INVALID_OUTPUT",
                    "schema",
                    "Tool output is invalid.",
                    {"tool_id": call.tool_id},
                    active_ledger,
                    active_checkpoint_callback,
                    event_sink,
                    executor_status="invalid_output",
                    external_execution_may_have_happened="unknown",
                )
            output = raw_result.value
            business_status = raw_result.business_status

        try:
            _validate_json_schema(output, definition.output_schema, "$")
        except ValidationError as error:
            details = {"tool_id": call.tool_id}
            path = error.details.get("path")
            if isinstance(path, str):
                details["path"] = path
            return _record_failed_and_persist(
                call,
                "TOOL_INVALID_OUTPUT",
                "schema",
                "Tool output is invalid.",
                details,
                active_ledger,
                active_checkpoint_callback,
                event_sink,
                executor_status="invalid_output",
                external_execution_may_have_happened="unknown",
            )

        result = ToolResult(
            call_id=call.call_id,
            tool_id=call.tool_id,
            status="ok",
            value=output,
            business_status=business_status,
        )
        try:
            active_ledger.record_completed(call, result)
        except ValidationError:
            # A successful executor result is not considered durable until the
            # ledger reaches a terminal state.  Convert an internal ledger
            # contract failure into a safe runtime error and leave the pending
            # record untouched if it cannot be closed.
            return _error_result(
                call,
                "TOOL_LEDGER_UPDATE_FAILED",
                "runtime",
                "Tool execution result could not be recorded.",
                {"tool_id": call.tool_id},
                event_sink,
                ledger=active_ledger,
                record_ledger=True,
            )
        return _persist_terminal(
            call,
            result,
            active_ledger,
            active_checkpoint_callback,
            event_sink,
            executor_status="succeeded",
            external_execution_may_have_happened=True,
        )


def create_retrieval_tool_runtime(
    search_executor: Callable[[str, dict[str, Any], int], Any],
    read_executor: Callable[[str], Any],
    ledger: ToolExecutionLedger | None = None,
    checkpoint_callback: ToolCheckpointCallback | None = None,
    decision_memory_executor: Callable[..., Any] | None = None,
    reuse_preflight: Callable[[], Any] | None = None,
    expected_source_context: dict[str, Any] | None = None,
    task_relation_tools: Any = None,
) -> ToolRuntime:
    """Compose provider-neutral, read-only retrieval tools at the runtime boundary.

    The public schemas contain only business arguments.  The trusted callbacks
    remain executor bindings and are never serialized into a ToolCall.
    """
    if not callable(search_executor):
        raise ValidationError("search_executor must be callable.")
    if not callable(read_executor):
        raise ValidationError("read_executor must be callable.")
    if decision_memory_executor is not None and not callable(decision_memory_executor):
        raise ValidationError("decision_memory_executor must be callable or None.")
    if expected_source_context is not None and not isinstance(expected_source_context, dict):
        raise ValidationError("expected_source_context must be an object or None.")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="search_notes",
            version="1",
            description=(
                "Search the authorized notes for source evidence. Use this before "
                "read_verified_note; results contain evidence_id values that can be "
                "read and cited. Copy every Runtime source_context identity field from "
                "retrieval_request exactly; only scope_path or task_plan subgoal fields may be added. "
                "For an independent subgoal use dependency_entities={} (never null). "
                "After search_notes returns an evidence_id, call read_verified_note on "
                "that ID before any search for a subgoal depending on it. "
                "If business_status is REPEATED_NO_PROGRESS, revise the query or "
                "return Final instead of repeating it."
            ),
            input_schema={
                "type": "object",
                "required": ["query", "source_context", "limit"],
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": r"(?s).*\S.*",
                    },
                    "source_context": {
                        "type": "object",
                        "description": "Copy the full Runtime source_context identity from retrieval_request; do not omit or alter workspace/index fields. Only scope_path and task_plan subgoal fields may be added.",
                        "properties": {
                            "scope_path": {
                                "type": ["string", "null"],
                                "description": "Optional logical directory within the selected source; null searches the whole source.",
                            },
                            "subgoal_id": {
                                "type": "string", "minLength": 1,
                                "description": "For a TaskPlan only: exact current subgoal ID from the plan.",
                            },
                            "dependency_entities": {
                                "type": "object",
                                "description": "For a TaskPlan only: each required parent subgoal ID mapped to one literal entity from its verified read.",
                            },
                            "query_variant": {
                                "type": "string", "enum": ["primary", "alternate"],
                                "description": "For a TaskPlan only: primary query or its one bounded alternate.",
                            },
                        },
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of search results to return.",
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "array",
                "items": {"type": "object"},
            },
        ),
        lambda arguments: search_executor(
            arguments["query"], arguments["source_context"], arguments["limit"]
        ),
    )
    registry.register(
        ToolDefinition(
            tool_id="read_verified_note",
            version="1",
            description=(
                "Read one verified evidence item. Pass the exact evidence_id returned "
                "by an earlier successful search_notes call in this run, never its relative_path or an invented ID."
            ),
            input_schema={
                "type": "object",
                "required": ["note_ref"],
                "properties": {
                    "note_ref": {
                        "type": "string",
                        "description": (
                            "The exact search_notes evidence_id to read; do not pass "
                            "relative_path, filename, or unobserved ID."
                        ),
                        "minLength": 1,
                        # Mirrors _require_relative_note_ref without exposing
                        # filesystem details to the public tool contract.
                        "pattern": r"(?is)^(?!.*gold)(?!.*\.\.)(?![/\\])(?![A-Za-z]:[\\/])(?=.*\S).+$",
                    }
                },
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        ),
        lambda arguments: read_executor(arguments["note_ref"]),
    )
    if decision_memory_executor is not None:
        registry.register(
            ToolDefinition(
                tool_id="search_decision_memory",
                version="1",
                description=(
                    "Search authorized temporal decision memory for navigation hints. "
                    "Memory is not source evidence; call search_notes or read_verified_note "
                    "before citing a material claim. In a run with a selected reviewed "
                    "decision scope, a query-only call returns only that selected state; "
                    "an explicit subject and relation must both match that scope. "
                    "In that scoped run, omit as_of or use exactly the selected "
                    "run as_of; a source event date never changes the allowed "
                    "lookup date. An out-of-scope as_of is rejected. "
                    "Its receipt and valid-time status "
                    "must still be checked against source reads in Final."
                ),
                input_schema={
                    "type": "object",
                    "required": ["query", "limit"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"(?s).*\S.*",
                        },
                        "as_of": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Optional timezone-aware ISO-8601 timestamp for historical lookup. "
                                "When this run has a selected decision scope, use only its exact "
                                "as_of or omit this field; source event dates do not authorize "
                                "another lookup date."
                            ),
                        },
                        "subject": {"type": "string", "minLength": 1, "maxLength": 512},
                        "relation": {"type": "string", "minLength": 1, "maxLength": 512},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "array",
                    "items": {"type": "object"},
                },
            ),
            lambda arguments: (
                decision_memory_executor(
                    arguments["query"], arguments.get("as_of"), arguments["limit"],
                    arguments["subject"], arguments["relation"],
                ) if "subject" in arguments else decision_memory_executor(
                    arguments["query"], arguments.get("as_of"), arguments["limit"],
                )
            ),
        )

    if task_relation_tools is not None:
        from linkloom.tools.task_relations import register_task_relation_tools
        register_task_relation_tools(registry, task_relation_tools)

    def validate_task_query_arguments(arguments: dict[str, Any]) -> None:
        if "as_of" in arguments:
            from datetime import date
            try:
                date.fromisoformat(arguments["as_of"])
            except ValueError as error:
                raise ValidationError("as_of must be a calendar date.", details={"path": "$.as_of"}) from error

    def validate_search_arguments(arguments: dict[str, Any]) -> None:
        source = arguments["source_context"]
        if expected_source_context is not None:
            for key, value in expected_source_context.items():
                # Null fixture metadata carries no identity. Every configured
                # identity must be copied exactly, including its JSON type.
                if key not in source and value is None:
                    continue
                if (key not in source or type(source[key]) is not type(value)
                        or source[key] != value):
                    raise ValidationError(
                        "source_context does not match the active Runtime source.",
                        details={"path": f"$.source_context.{key}", "reason": "semantic_mismatch"},
                    )
            allowed = set(expected_source_context) | {
                "scope_path", "subgoal_id", "dependency_entities", "query_variant",
            }
            if set(source) - allowed:
                raise ValidationError(
                    "source_context contains unsupported fields.",
                    details={"path": "$.source_context", "reason": "semantic_mismatch"},
                )
        subgoal = source.get("subgoal_id")
        if subgoal is not None and (not isinstance(subgoal, str) or not subgoal.strip()):
            raise ValidationError(
                "subgoal_id must be a non-empty plan identifier.",
                details={"path": "$.source_context.subgoal_id", "reason": "semantic_mismatch"},
            )
        dependencies = source.get("dependency_entities")
        if dependencies is not None and (
            not isinstance(dependencies, dict)
            or any(
                not isinstance(key, str) or not key.strip()
                or not isinstance(value, str) or not value.strip()
                for key, value in dependencies.items()
            )
        ):
            raise ValidationError(
                "dependency_entities must map subgoal IDs to verified entity literals.",
                details={"path": "$.source_context.dependency_entities", "reason": "semantic_mismatch"},
            )
        if source.get("query_variant") not in (None, "primary", "alternate"):
            raise ValidationError(
                "query_variant must be primary or alternate.",
                details={"path": "$.source_context.query_variant", "reason": "semantic_mismatch"},
            )
        scope_path = source.get("scope_path")
        if scope_path is None:
            return
        if (
            "\\" in scope_path
            or "\r" in scope_path
            or "\n" in scope_path
            or scope_path.startswith("//")
            or re.match(r"^[A-Za-z]:", scope_path)
            or ".." in scope_path.split("/")
        ):
            raise ValidationError(
                "scope_path must be a logical path within the selected source.",
                details={"path": "$.source_context.scope_path"},
            )

    def validate_read_arguments(arguments: dict[str, Any]) -> None:
        ref = arguments["note_ref"]
        if "/" in ref or "\\" in ref or ref.lower().endswith(".md"):
            raise ValidationError(
                "note_ref must be an observed evidence ID, not a path.",
                details={"path": "$.note_ref", "reason": "semantic_mismatch"},
            )

    def validate_decision_memory_arguments(arguments: dict[str, Any]) -> None:
        if ("subject" in arguments) != ("relation" in arguments):
            raise ValidationError(
                "Reviewed decision lookup requires both subject and relation.",
                details={"path": "$.subject", "reason": "semantic_mismatch"},
            )
        try:
            parse_decision_memory_as_of(arguments.get("as_of"))
        except ValueError as error:
            raise ValidationError(
                "Decision-memory as_of must be a timezone-aware ISO-8601 datetime.",
                details={"path": "$.as_of"},
            ) from error

    return ToolRuntime(
        registry,
        ledger=ledger,
        checkpoint_callback=checkpoint_callback,
        argument_validators={
            **({"query_task_relations": validate_task_query_arguments} if task_relation_tools is not None else {}),
            "search_notes": validate_search_arguments,
            "read_verified_note": validate_read_arguments,
            **(
                {"search_decision_memory": validate_decision_memory_arguments}
                if decision_memory_executor is not None
                else {}
            ),
        },
        reuse_preflight=reuse_preflight,
    )


def _safe_exception_diagnostic(error: BaseException) -> dict[str, Any]:
    """Capture executor failure location and exception types without messages."""
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    location = None
    if frames:
        origin = frames[-1]
        location = {
            "file": origin.filename.replace("\\", "/").rsplit("/", 1)[-1],
            "function": origin.name,
            "line": origin.lineno,
        }

    exception_chain: list[dict[str, str]] = []
    current: BaseException | None = error
    relationship = "raised"
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(exception_chain) < 8:
        seen.add(id(current))
        exception_chain.append(
            {"relationship": relationship, "exception_type": type(current).__name__}
        )
        if current.__cause__ is not None:
            current = current.__cause__
            relationship = "cause"
        elif current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
            relationship = "context"
        else:
            break

    return {
        "stage": "executor",
        "exception_type": type(error).__name__,
        "location": location,
        "exception_chain": exception_chain,
    }


__all__ = [
    "ToolCheckpointCallback",
    "ToolRuntime",
    "ToolExecutionLedger",
    "create_retrieval_tool_runtime",
]

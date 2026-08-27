"""Single-call runtime lifecycle for registered Linkloom tools."""

from __future__ import annotations

import re
from typing import Any, Callable

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import _assert_json_safe_primitive
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.tool_policy import ToolPolicyEnforcer


_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
ToolCheckpointCallback = Callable[[ToolExecutionLedger], None]


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


class ToolRuntime:
    """Execute exactly one ToolCall through a registry and policy enforcer."""

    def __init__(
        self,
        registry: ToolRegistry,
        ledger: ToolExecutionLedger | None = None,
        checkpoint_callback: ToolCheckpointCallback | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise ValidationError("ToolRuntime registry must be a ToolRegistry.")
        if ledger is not None and not isinstance(ledger, ToolExecutionLedger):
            raise ValidationError("ToolRuntime ledger must be a ToolExecutionLedger or None.")
        if checkpoint_callback is not None and not callable(checkpoint_callback):
            raise ValidationError("ToolRuntime checkpoint_callback must be callable or None.")
        self.registry = registry
        self.ledger = ledger or ToolExecutionLedger()
        self.checkpoint_callback = checkpoint_callback

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
            _validate_json_schema(call.arguments, definition.input_schema, "$")
        except ValidationError as error:
            details = {"tool_id": call.tool_id}
            path = error.details.get("path")
            if isinstance(path, str):
                details["path"] = path
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
            raw_result = executor(call.arguments)
        except Exception:
            return _record_failed_and_persist(
                call,
                "TOOL_EXECUTION_FAILED",
                "runtime",
                "Tool execution failed.",
                {"tool_id": call.tool_id},
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
) -> ToolRuntime:
    """Compose the two read-only retrieval tools at the runtime boundary.

    The public schemas contain only business arguments.  The trusted callbacks
    remain executor bindings and are never serialized into a ToolCall.
    """
    if not callable(search_executor):
        raise ValidationError("search_executor must be callable.")
    if not callable(read_executor):
        raise ValidationError("read_executor must be callable.")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="search_notes",
            version="1",
            description="Search verified notes using a query and source context.",
            input_schema={
                "type": "object",
                "required": ["query", "source_context", "limit"],
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": r"(?s).*\S.*",
                    },
                    "source_context": {"type": "object"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
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
            description="Read one verified note reference.",
            input_schema={
                "type": "object",
                "required": ["note_ref"],
                "properties": {
                    "note_ref": {
                        "type": "string",
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
    return ToolRuntime(
        registry,
        ledger=ledger,
        checkpoint_callback=checkpoint_callback,
    )


__all__ = [
    "ToolCheckpointCallback",
    "ToolRuntime",
    "ToolExecutionLedger",
    "create_retrieval_tool_runtime",
]

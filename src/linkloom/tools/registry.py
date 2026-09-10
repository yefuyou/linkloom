"""Definition and executor-binding registry for Linkloom tools.

The registry deliberately does not execute tools or make permission, budget,
retry, timeout, tracing, or planning decisions. Those responsibilities belong
to later runtime layers.
"""

from __future__ import annotations

from typing import Any, Callable, TypeAlias

from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolDefinition


ToolExecutor: TypeAlias = Callable[..., Any]
ToolBinding: TypeAlias = tuple[ToolDefinition, ToolExecutor]


class ToolRegistry:
    """Register and resolve static tool definitions and executor bindings."""

    def __init__(self) -> None:
        self._bindings: dict[str, ToolBinding] = {}

    def register(self, definition: ToolDefinition, executor: ToolExecutor) -> None:
        """Register one tool definition and its callable binding.

        Registration stores the supplied definition and executor by reference;
        it does not mutate either object and does not invoke the executor.
        """
        if not isinstance(definition, ToolDefinition):
            raise ValidationError("ToolRegistry definition must be a ToolDefinition.")
        if not callable(executor):
            raise ValidationError("ToolRegistry executor must be callable.")
        if definition.tool_id in self._bindings:
            raise ValidationError(
                f"Tool '{definition.tool_id}' is already registered.",
                details={"reason": "duplicate_tool", "tool_id": definition.tool_id},
            )
        self._bindings[definition.tool_id] = (definition, executor)

    def resolve(self, tool_id: str) -> ToolBinding:
        """Return the registered definition and executor for ``tool_id``."""
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValidationError("ToolRegistry tool_id must be a non-empty string.")
        binding = self._bindings.get(tool_id)
        if binding is None:
            raise ValidationError(
                f"Unknown tool '{tool_id}'.",
                details={"reason": "unknown_tool", "tool_id": tool_id},
            )
        return binding


__all__ = ["ToolBinding", "ToolExecutor", "ToolRegistry"]

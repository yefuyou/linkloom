import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolDefinition
from linkloom.tools.registry import ToolRegistry


def _definition(tool_id: str = "search_notes") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1",
        description="Search verified notes.",
        input_schema={"type": "object", "required": ["query"]},
        output_schema={"type": "array"},
    )


def test_register_and_resolve_returns_definition_and_executor_binding():
    registry = ToolRegistry()
    definition = _definition()
    executor = lambda arguments: arguments

    registry.register(definition, executor)

    resolved_definition, resolved_executor = registry.resolve("search_notes")

    assert resolved_definition is definition
    assert resolved_executor is executor


def test_resolve_does_not_execute_the_bound_executor():
    registry = ToolRegistry()
    definition = _definition()
    calls = []

    def executor(arguments):
        calls.append(arguments)
        return {"ok": True}

    registry.register(definition, executor)
    registry.resolve(definition.tool_id)

    assert calls == []


def test_duplicate_registration_is_rejected_without_replacing_binding():
    registry = ToolRegistry()
    definition = _definition()
    first_executor = lambda arguments: {"source": "first"}
    second_executor = lambda arguments: {"source": "second"}

    registry.register(definition, first_executor)

    with pytest.raises(ValidationError, match="already registered") as exc_info:
        registry.register(definition, second_executor)

    assert exc_info.value.details == {
        "reason": "duplicate_tool",
        "tool_id": "search_notes",
    }
    _, resolved_executor = registry.resolve("search_notes")
    assert resolved_executor is first_executor


def test_unknown_tool_lookup_is_rejected_with_stable_details():
    registry = ToolRegistry()

    with pytest.raises(ValidationError, match="Unknown tool") as exc_info:
        registry.resolve("read_verified_note")

    assert exc_info.value.details == {
        "reason": "unknown_tool",
        "tool_id": "read_verified_note",
    }


def test_registry_does_not_modify_tool_definition():
    registry = ToolRegistry()
    definition = _definition()
    before = definition.to_dict()

    registry.register(definition, lambda arguments: arguments)
    registry.resolve(definition.tool_id)

    assert definition.to_dict() == before


def test_register_requires_a_callable_executor():
    registry = ToolRegistry()

    with pytest.raises(ValidationError, match="callable"):
        registry.register(_definition(), object())

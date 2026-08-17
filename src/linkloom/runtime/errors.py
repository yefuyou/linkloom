"""Structured errors for Linkloom Runtime control plane."""

from __future__ import annotations

from typing import Any

VALID_ERROR_CATEGORIES = {
    "path",
    "input",
    "schema",
    "provider",
    "budget",
    "runtime",
    "evaluation",
    "permission",
    "write",
}


class RuntimeModelError(Exception):
    """Base error for all Linkloom runtime exceptions."""

    def __init__(
        self,
        message: str,
        code: str = "RUNTIME_ERROR",
        category: str = "runtime",
        retryable: bool = False,
        affected_refs: list[str] | None = None,
        details: dict[str, Any] | None = None,
        safe_to_expose: bool = True,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.category = category
        self.retryable = retryable
        self.affected_refs = affected_refs or []
        self.details = details or {}
        self.safe_to_expose = safe_to_expose

    def to_envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "retryable": self.retryable,
            "affected_refs": self.affected_refs,
            "details": self.details,
            "safe_to_expose": self.safe_to_expose,
        }


class StateTransitionError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="STATE_TRANSITION_INVALID",
            category="runtime",
            retryable=False,
            details=details,
        )


class ValidationError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="SCHEMA_VALIDATION_ERROR",
            category="schema",
            retryable=False,
            details=details,
        )


class CheckpointError(RuntimeModelError):
    def __init__(
        self,
        message: str,
        code: str = "CHECKPOINT_ERROR",
        category: str = "runtime",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            category=category,
            retryable=retryable,
            details=details,
        )


class CheckpointNotFoundError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="CHECKPOINT_NOT_FOUND",
            category="runtime",
            retryable=False,
            details=details,
        )


class CheckpointWriteError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="CHECKPOINT_WRITE_FAILED",
            category="runtime",
            retryable=False,
            details=details,
        )


class CheckpointCorruptError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="CHECKPOINT_CORRUPT",
            category="runtime",
            retryable=False,
            details=details,
        )


class ThreadNotFoundError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="THREAD_NOT_FOUND",
            category="runtime",
            retryable=False,
            details=details,
        )


class InterruptNotFoundError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="INTERRUPT_NOT_FOUND",
            category="runtime",
            retryable=False,
            details=details,
        )


class InterruptResponseInvalidError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="INTERRUPT_RESPONSE_INVALID",
            category="input",
            retryable=False,
            details=details,
        )


class ThreadBusyError(CheckpointError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="THREAD_BUSY",
            category="runtime",
            retryable=False,
            details=details,
        )


class RuntimeSchemaIncompatibleError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="RUNTIME_SCHEMA_INCOMPATIBLE",
            category="schema",
            retryable=False,
            details=details,
        )


class StaleSourceError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="CONTENT_CHANGED",
            category="runtime",
            retryable=False,
            details=details,
        )


class PermissionDeniedError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="PERMISSION_DENIED",
            category="permission",
            retryable=False,
            details=details,
        )


class BudgetExceededError(RuntimeModelError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="BUDGET_EXCEEDED",
            category="budget",
            retryable=False,
            details=details,
        )

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

_Metadata: TypeAlias = Mapping[str, object]


class OperationResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ContractError:
    code: str
    message: str
    retryable: bool = False
    metadata: _Metadata = field(default_factory=dict)


class ContractViolation(ValueError):
    pass


def require_identifier(value: str, name: str) -> None:
    if not value or len(value) > 512:
        raise ContractViolation(f"{name} must be a non-empty string of at most 512 characters")


class OperationErrorCode(StrEnum):
    INVALID_OPERATION = "invalid_operation"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    ADAPTER_FAILURE = "adapter_failure"
    UNAUTHORIZED_DESTINATION = "unauthorized_destination"
    REQUEST_DUPLICATE = "request_duplicate"
    REQUEST_RESOLVED = "request_resolved"
    REQUEST_STALE = "request_stale"


class _MappedOperationError(RuntimeError):
    """Owner-specific error carrying only its stable common projection code."""

    operation_error_code: OperationErrorCode


def operation_error(
    error: Exception,
    *,
    code: OperationErrorCode | None = None,
) -> ContractError:
    """Project native exceptions onto the stable common operation error vocabulary."""
    if code is None:
        if isinstance(error, NotImplementedError):
            code = OperationErrorCode.UNSUPPORTED
        elif isinstance(error, KeyError):
            code = OperationErrorCode.NOT_FOUND
        elif isinstance(error, ValueError):
            code = OperationErrorCode.INVALID_OPERATION
        elif isinstance(error, _MappedOperationError):
            code = error.operation_error_code
        else:
            code = OperationErrorCode.ADAPTER_FAILURE
    return ContractError(
        code=code.value,
        message=str(error) or code.value,
        metadata={"native_exception": type(error).__name__},
    )


__all__ = [
    "ContractError",
    "ContractViolation",
    "OperationErrorCode",
    "OperationResultStatus",
    "operation_error",
    "require_identifier",
]

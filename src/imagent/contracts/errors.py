from __future__ import annotations

from enum import StrEnum

from .model import ContractError


class OperationErrorCode(StrEnum):
    INVALID_OPERATION = "invalid_operation"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    ADAPTER_FAILURE = "adapter_failure"


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
        else:
            code = OperationErrorCode.ADAPTER_FAILURE
    return ContractError(
        code=code.value,
        message=str(error) or code.value,
        metadata={"native_exception": type(error).__name__},
    )

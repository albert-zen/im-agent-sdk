from __future__ import annotations

from enum import StrEnum

from .model import ContractError


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


class RequestDuplicateError(RuntimeError):
    pass


class RequestResolvedError(RuntimeError):
    pass


class RequestStaleError(RuntimeError):
    pass


class ApplicationInputOutcomeUnknown(RuntimeError):
    """Native input dispatch may have succeeded, so automatic retry is unsafe."""

    def __init__(self, message: str, cause: BaseException) -> None:
        super().__init__(message)
        self.cause = cause


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
        elif isinstance(error, RequestDuplicateError):
            code = OperationErrorCode.REQUEST_DUPLICATE
        elif isinstance(error, RequestResolvedError):
            code = OperationErrorCode.REQUEST_RESOLVED
        elif isinstance(error, RequestStaleError):
            code = OperationErrorCode.REQUEST_STALE
        else:
            code = OperationErrorCode.ADAPTER_FAILURE
    return ContractError(
        code=code.value,
        message=str(error) or code.value,
        metadata={"native_exception": type(error).__name__},
    )

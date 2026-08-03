from __future__ import annotations

from ..interaction.operations import (
    OperationErrorCode as _OperationErrorCode,
)
from ..interaction.operations import (
    _MappedOperationError,
)


class RequestDuplicateError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_DUPLICATE


class RequestResolvedError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_RESOLVED


class RequestStaleError(_MappedOperationError):
    operation_error_code = _OperationErrorCode.REQUEST_STALE


class ApplicationInputOutcomeUnknown(RuntimeError):
    """Native input dispatch may have succeeded, so automatic retry is unsafe."""

    def __init__(self, message: str, cause: BaseException) -> None:
        super().__init__(message)
        self.cause = cause

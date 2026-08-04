from __future__ import annotations


class ApplicationInputOutcomeUnknown(RuntimeError):
    """Native input dispatch may have succeeded, so automatic retry is unsafe."""

    def __init__(self, message: str, cause: BaseException) -> None:
        super().__init__(message)
        self.cause = cause

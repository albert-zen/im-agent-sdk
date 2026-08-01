from __future__ import annotations

from .model import ThreadRef


class ContractViolation(ValueError):
    pass


def require_identifier(value: str, name: str) -> None:
    if not value or len(value) > 512:
        raise ContractViolation(f"{name} must be a non-empty string of at most 512 characters")


def validate_thread_ref(thread: ThreadRef) -> None:
    require_identifier(
        thread.application_instance_id,
        "application_instance_id",
    )
    require_identifier(thread.native_thread_id, "native_thread_id")
    if (
        thread.project_ref is not None
        and thread.project_ref.application_instance_id != thread.application_instance_id
    ):
        raise ContractViolation("thread and project belong to different application instances")

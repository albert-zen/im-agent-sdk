from __future__ import annotations

from ..interaction.operations import ContractViolation, require_identifier
from .model import ThreadRef


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

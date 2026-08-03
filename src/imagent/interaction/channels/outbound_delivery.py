from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class OutboundArtifact:
    kind: Literal["image", "file"]
    local_path: str
    content_type: str
    filename: str
    size_bytes: int
    sha256: str = ""
    attachment_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeDeliveryResult:
    native_message_ids: tuple[str, ...] = ()


class PermanentArtifactDeliveryError(RuntimeError):
    """An artifact cannot be delivered and retrying the same bytes will not help."""


@dataclass(frozen=True, slots=True)
class ArtifactDeliveryReceipt:
    platform_message_id: str = ""
    delivery_identity: str = ""


@dataclass(slots=True)
class OutboundMessage:
    channel_id: str
    conversation_id: str
    message_type: str
    text: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: list[OutboundArtifact] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.artifacts = [
            item if isinstance(item, OutboundArtifact) else OutboundArtifact(**item)
            for item in self.artifacts
        ]


def split_text(text: str, *, limit: int) -> list[str]:
    """Split text for an IM platform without splitting Unicode code points."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = text.strip()
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        minimum_soft_break = max(1, limit // 2)
        break_at = max(
            window.rfind("\n\n", minimum_soft_break, limit + 1),
            window.rfind("\n", minimum_soft_break, limit + 1),
            window.rfind(" ", minimum_soft_break, limit + 1),
        )
        if break_at < minimum_soft_break:
            break_at = limit
        chunk = remaining[:break_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            break_at = limit
        chunks.append(chunk)
        remaining = remaining[break_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def deliver_artifact_batch(
    message: OutboundMessage,
    send_one: Callable[
        [OutboundArtifact],
        Awaitable[ArtifactDeliveryReceipt | None],
    ],
) -> None:
    """Deliver artifacts in order for one native-message attempt.

    A cancellation or unclassified failure retains the failed artifact and the
    unattempted suffix on ``message.artifacts`` as truthful in-memory evidence
    for the caller. This helper does not own a durable outbox or authorize
    replay. Known permanent failures become a text notice and do not block the
    remaining artifacts.
    """

    failures: list[str] = []
    original_artifacts = list(message.artifacts)
    for index, artifact in enumerate(original_artifacts):
        try:
            receipt = await send_one(artifact)
        except PermanentArtifactDeliveryError as exc:
            error = str(exc)
            failures.append(f"{artifact.filename}: {error}")
            record_artifact_failure(message, artifact, error=error)
        except asyncio.CancelledError:
            append_artifact_failures(message, failures)
            message.artifacts = original_artifacts[index:]
            raise
        except Exception:
            append_artifact_failures(message, failures)
            message.artifacts = original_artifacts[index:]
            raise
        else:
            receipt = receipt or ArtifactDeliveryReceipt()
            record_artifact_delivery(
                message,
                artifact,
                platform_message_id=receipt.platform_message_id,
                delivery_identity=receipt.delivery_identity,
            )
        message.artifacts = original_artifacts[index + 1 :]
    message.artifacts = []
    append_artifact_failures(message, failures)


async def read_managed_artifact(
    artifact: OutboundArtifact,
    *,
    root: str | Path,
) -> tuple[Path, bytes]:
    """Read one verified artifact beneath a caller-managed trusted root."""

    try:
        managed_root = Path(root).resolve(strict=True)
        source = Path(artifact.local_path).resolve(strict=True)
        source.relative_to(managed_root)
    except (OSError, ValueError) as exc:
        raise PermanentArtifactDeliveryError(
            "artifact is outside the trusted root or no longer exists"
        ) from exc
    if not source.is_file():
        raise PermanentArtifactDeliveryError("artifact is no longer a regular file")
    try:
        content = await asyncio.to_thread(source.read_bytes)
    except OSError as exc:
        raise PermanentArtifactDeliveryError("artifact can no longer be read") from exc
    if len(content) != artifact.size_bytes:
        raise PermanentArtifactDeliveryError("artifact changed after it was staged")
    digest = await asyncio.to_thread(hashlib.sha256, content)
    if artifact.sha256 and digest.hexdigest() != artifact.sha256:
        raise PermanentArtifactDeliveryError("artifact changed after it was staged")
    return source, content


def append_artifact_failures(message: OutboundMessage, failures: list[str]) -> None:
    if not failures:
        return
    notice = "Attachment delivery unavailable:\n" + "\n".join(
        f"- {failure}" for failure in failures
    )
    if notice not in message.text:
        message.text = "\n\n".join(part for part in (message.text, notice) if part)
    recorded = message.metadata.setdefault("artifact_failures", [])
    if isinstance(recorded, list):
        for failure in failures:
            if failure not in recorded:
                recorded.append(failure)


def stable_artifact_identity(
    message: OutboundMessage,
    artifact: OutboundArtifact,
) -> str | None:
    delivery_id = str(message.metadata.get("delivery_id") or "").strip()
    if not delivery_id:
        return None
    digest = hashlib.sha256(
        (
            f"{delivery_id}\0{artifact.attachment_id or artifact.sha256 or artifact.local_path}"
        ).encode()
    ).hexdigest()
    return digest


def record_artifact_delivery(
    message: OutboundMessage,
    artifact: OutboundArtifact,
    *,
    platform_message_id: str = "",
    delivery_identity: str = "",
) -> None:
    receipts = message.metadata.setdefault("artifact_receipts", [])
    if not isinstance(receipts, list):
        return
    receipts.append(
        {
            "attachment_id": artifact.attachment_id,
            "filename": artifact.filename,
            "sha256": artifact.sha256,
            "local_path": artifact.local_path,
            "status": "delivered",
            "platform_message_id": platform_message_id,
            "delivery_identity": delivery_identity,
        }
    )


def record_artifact_failure(
    message: OutboundMessage,
    artifact: OutboundArtifact,
    *,
    error: str,
) -> None:
    receipts = message.metadata.setdefault("artifact_receipts", [])
    if not isinstance(receipts, list):
        return
    receipts.append(
        {
            "attachment_id": artifact.attachment_id,
            "filename": artifact.filename,
            "sha256": artifact.sha256,
            "local_path": artifact.local_path,
            "status": "failed",
            "error": error,
            "platform_message_id": "",
            "delivery_identity": "",
        }
    )


def delivered_artifact_message_ids(message: OutboundMessage) -> tuple[str, ...]:
    receipts = message.metadata.get("artifact_receipts")
    if not isinstance(receipts, list):
        return ()
    message_ids: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("status") != "delivered":
            continue
        message_id = str(receipt.get("platform_message_id") or "").strip()
        if message_id and message_id not in message_ids:
            message_ids.append(message_id)
    return tuple(message_ids)

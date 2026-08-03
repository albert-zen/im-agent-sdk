from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias


class AttachmentSourceKind(StrEnum):
    LOCAL_PATH = "local_path"
    REMOTE_URL = "remote_url"
    ATTACHMENT_HANDLE = "attachment_handle"


class AttachmentGrouping(StrEnum):
    NONE = "none"
    SAME_MEDIA_FAMILY = "same_media_family"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class LocalPath:
    path: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.LOCAL_PATH,
    )


@dataclass(frozen=True, slots=True)
class RemoteUrl:
    url: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.REMOTE_URL,
    )


@dataclass(frozen=True, slots=True)
class AttachmentHandle:
    handle_id: str
    kind: AttachmentSourceKind = field(
        init=False,
        default=AttachmentSourceKind.ATTACHMENT_HANDLE,
    )


AttachmentSource: TypeAlias = LocalPath | RemoteUrl | AttachmentHandle


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    attachment_id: str
    media_type: str
    source: AttachmentSource
    filename: str | None = None
    size_bytes: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def configure_shared_filesystem_root(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    try:
        root = Path(value).resolve(strict=True)
    except OSError as error:
        raise ValueError("shared_filesystem_root does not exist") from error
    if not root.is_dir():
        raise ValueError("shared_filesystem_root must be a directory")
    return root


def resolve_local_attachment(
    source: AttachmentSource,
    *,
    shared_filesystem_root: Path | None,
    consumer: str,
) -> Path:
    if not isinstance(source, LocalPath):
        raise NotImplementedError(
            f"{consumer} does not support attachment source {source.kind.value}"
        )
    if shared_filesystem_root is None:
        raise ValueError(f"{consumer} LocalPath requires a configured shared_filesystem_root")
    candidate = Path(source.path)
    if not candidate.is_absolute():
        raise ValueError(f"{consumer} LocalPath must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(shared_filesystem_root)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"{consumer} LocalPath is outside the trusted shared_filesystem_root"
        ) from error
    if not resolved.is_file():
        raise ValueError(f"{consumer} LocalPath must reference a regular file")
    return resolved


__all__ = [
    "AttachmentContent",
    "AttachmentGrouping",
    "AttachmentHandle",
    "AttachmentSource",
    "AttachmentSourceKind",
    "LocalPath",
    "RemoteUrl",
    "configure_shared_filesystem_root",
    "resolve_local_attachment",
]

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

TEXT_FILE_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".jsx": "text/jsx",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".less": "text/x-less",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".fish": "text/x-shellscript",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cc": "text/x-c++",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".java": "text/x-java-source",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".sql": "application/sql",
    ".graphql": "application/graphql",
    ".proto": "text/x-protobuf",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".csv": "text/csv",
    ".log": "text/plain",
}


class UnsupportedGenericFileError(ValueError):
    pass


class InvalidGenericFileError(ValueError):
    pass


def detect_generic_file(filename: str, content: bytes) -> tuple[str, str]:
    """Detect the transferred safe file subset from name and actual bytes."""

    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf":
        tail = content[-2048:].rstrip()
        if (
            re.match(rb"%PDF-[12]\.\d(?:\r?\n|\r)", content) is None
            or re.search(rb"\d+\s+\d+\s+obj\b", content) is None
            or b"endobj" not in content
            or b"startxref" not in tail
            or not tail.endswith(b"%%EOF")
            or (
                re.search(rb"(?:^|\r?\n)xref(?:\r?\n|\r)", content) is None
                and re.search(rb"/Type\s*/XRef\b", content) is None
            )
        ):
            raise InvalidGenericFileError
        return "application/pdf", suffix
    content_type = TEXT_FILE_CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise UnsupportedGenericFileError
    if b"\x00" in content:
        raise InvalidGenericFileError
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidGenericFileError from None
    return content_type, suffix


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

from __future__ import annotations

import os
import re
import stat
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


def read_local_attachment(
    source: AttachmentSource,
    *,
    shared_filesystem_root: Path | None,
    consumer: str,
    expected_size: int,
    max_bytes: int,
) -> bytes:
    """Read one rooted regular file through a no-follow descriptor chain."""

    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
        raise ValueError(f"{consumer} LocalPath expected_size must be a non-negative integer")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError(f"{consumer} LocalPath max_bytes must be a positive integer")
    if expected_size > max_bytes:
        raise ValueError(f"{consumer} LocalPath exceeds its byte bound")
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
        # Resolve only the parent to normalize platform aliases such as
        # /var -> /private/var. Every component is then reopened relative to
        # the trusted root with O_NOFOLLOW, so a later swap can only fail.
        parent = candidate.parent.resolve(strict=True)
        relative_parent = parent.relative_to(shared_filesystem_root)
        relative = relative_parent / candidate.name
    except (OSError, ValueError) as error:
        raise ValueError(
            f"{consumer} LocalPath is outside the trusted shared_filesystem_root"
        ) from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{consumer} LocalPath contains an unsafe path component")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise NotImplementedError(f"{consumer} cannot safely acquire rooted LocalPath bytes")
    descriptors: list[int] = []
    try:
        current = os.open(shared_filesystem_root, os.O_RDONLY | directory | no_follow)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | directory | no_follow,
                dir_fd=current,
            )
            descriptors.append(current)
        payload_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | no_follow,
            dir_fd=current,
        )
        descriptors.append(payload_fd)
        details = os.fstat(payload_fd)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError(f"{consumer} LocalPath must reference a regular file")
        if details.st_size != expected_size:
            raise ValueError(f"{consumer} LocalPath declared size does not match bytes")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(payload_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != expected_size:
            raise ValueError(f"{consumer} LocalPath declared size does not match bytes")
        return payload
    except OSError as error:
        raise ValueError(
            f"{consumer} LocalPath is outside the trusted shared_filesystem_root"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


__all__ = [
    "AttachmentContent",
    "AttachmentGrouping",
    "AttachmentHandle",
    "AttachmentSource",
    "AttachmentSourceKind",
    "LocalPath",
    "RemoteUrl",
    "configure_shared_filesystem_root",
    "read_local_attachment",
    "resolve_local_attachment",
]

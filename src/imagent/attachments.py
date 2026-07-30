from __future__ import annotations

from pathlib import Path

from .contracts import AttachmentSource, LocalPath


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

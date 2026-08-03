from __future__ import annotations

import base64
import binascii
import hashlib
import os
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .media import AttachmentContent, LocalPath


@dataclass(frozen=True, slots=True)
class InlineArtifactStagingInput:
    attachment_id: str
    filename: str
    media_type: str
    encoded_content: str
    declared_size: int | None


def decoded_inline_artifact_size(encoded: str, *, max_bytes: int) -> int:
    if max_bytes < 0 or len(encoded) > 4 * ((max_bytes + 2) // 3) + 4:
        raise ValueError("inline artifact payload exceeds configured byte limit")
    try:
        decoded_size = len(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError) as error:
        raise ValueError("inlineArtifact.contentBase64 is invalid") from error
    if decoded_size > max_bytes:
        raise ValueError("inline artifact payload exceeds configured byte limit")
    return decoded_size


def validated_inline_artifact_size(
    artifact: InlineArtifactStagingInput,
    *,
    max_bytes: int,
    declared_size_error: str = "inline artifact declared size does not match encoded content",
) -> int:
    decoded_size = decoded_inline_artifact_size(
        artifact.encoded_content,
        max_bytes=max_bytes,
    )
    if artifact.declared_size is not None and artifact.declared_size != decoded_size:
        raise ValueError(declared_size_error)
    return decoded_size


def create_inline_staging_directory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    trusted_root = root.resolve(strict=True)
    if not trusted_root.is_dir():
        raise OSError("artifact staging root is not a directory")
    candidate = Path(
        tempfile.mkdtemp(
            prefix=".imagent-delivery-",
            dir=trusted_root,
        )
    )
    resolved = candidate.resolve(strict=True)
    resolved.relative_to(trusted_root)
    if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
        shutil.rmtree(candidate, ignore_errors=True)
        raise OSError("artifact staging directory cannot be a link")
    return resolved


def stage_inline_artifacts(
    directory: Path,
    artifacts: Sequence[InlineArtifactStagingInput],
) -> tuple[AttachmentContent, ...]:
    resolved_root = directory.resolve(strict=True)
    staged: list[AttachmentContent] = []
    for artifact in artifacts:
        try:
            content = base64.b64decode(artifact.encoded_content, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("inlineArtifact.contentBase64 is invalid") from error
        digest = hashlib.sha256(content).hexdigest()
        filename = f"{_safe_component(artifact.attachment_id)}-{digest}"
        target = (resolved_root / filename).resolve()
        target.relative_to(resolved_root)
        temporary = resolved_root / f".{filename}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        staged.append(
            AttachmentContent(
                attachment_id=artifact.attachment_id,
                media_type=artifact.media_type,
                source=LocalPath(str(target)),
                filename=artifact.filename,
                size_bytes=len(content),
                metadata={"sha256": digest},
            )
        )
    return tuple(staged)


def _safe_component(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

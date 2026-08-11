"""One public, production-shaped composition for the neutral consumer."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from imagent import (
    Gateway,
    GatewayExtensions,
    GatewayLimits,
    GatewayStore,
    MemoryGatewayStore,
    ProjectionPolicy,
)
from imagent.gateway.delivery import (
    DeliveryAuthorizer,
    DeliveryOutcome,
    DeliveryOutcomeContext,
    DeliveryOutcomeObserver,
)
from imagent.interaction.channels import DeliveryReceiptStatus
from imagent.interaction.controllers import CommandRegistry, MarkdownRequestPresenter
from imagent.interaction.media import AttachmentContent, LocalPath

from .application import ReferenceApplication
from .interaction import ReferenceChannel, ReferenceStatusService, build_command_registry


@dataclass(slots=True)
class ReferenceConsumer:
    """The local adapters and the one SDK-owned Gateway runtime."""

    channel: ReferenceChannel
    application: ReferenceApplication
    registry: CommandRegistry
    gateway: Gateway


class ReferenceArtifactLedger:
    """Bounded consumer-owned artifact bytes, leases, and restart cleanup."""

    _MAX_LEDGER_BYTES = 64 * 1024
    _OWNED_PAYLOAD = re.compile(r"artifact-[0-9a-f]{64}\.(?:txt|part)\Z")

    def __init__(self, root: str | Path, *, max_leases: int = 8) -> None:
        if (
            not isinstance(max_leases, int)
            or isinstance(max_leases, bool)
            or not 1 <= max_leases <= 64
        ):
            raise ValueError("artifact ledger max_leases must be between 1 and 64")
        self._root = Path(root).resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._ledger_path = self._root / "consumer-artifact-ledger.json"
        self._max_leases = max_leases
        self._records = self._load()

    @property
    def active_leases(self) -> int:
        return len(self._records)

    def stage(
        self,
        artifact_id: str,
        payload: bytes,
        *,
        expected_destinations: int,
    ) -> AttachmentContent:
        if not artifact_id or len(artifact_id) > 128:
            raise ValueError("artifact_id must be bounded text")
        if (
            not isinstance(expected_destinations, int)
            or isinstance(expected_destinations, bool)
            or expected_destinations < 1
            or expected_destinations > 64
        ):
            raise ValueError("expected_destinations must be between 1 and 64")
        if not payload or len(payload) > 1_024:
            raise ValueError("reference artifact bytes must be non-empty and bounded")
        if artifact_id not in self._records and len(self._records) >= self._max_leases:
            raise RuntimeError("consumer artifact ledger capacity is exhausted")
        if artifact_id in self._records:
            raise RuntimeError("consumer artifact lease is already active")
        path = self._root / f"artifact-{hashlib.sha256(artifact_id.encode()).hexdigest()}.txt"
        temporary = path.with_suffix(".part")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    raise OSError("consumer artifact payload write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._fsync_root()
        digest = hashlib.sha256(payload).hexdigest()
        self._records[artifact_id] = {
            "path": str(path),
            "sha256": digest,
            "size": len(payload),
            "expected": expected_destinations,
            "observed": 0,
        }
        self._persist()
        return AttachmentContent(
            attachment_id=artifact_id,
            media_type="text/plain",
            source=LocalPath(str(path)),
            filename=f"{artifact_id}.txt",
            size_bytes=len(payload),
            metadata={"sha256": digest},
        )

    async def observe_delivery_outcome(
        self,
        context: DeliveryOutcomeContext,
        outcome: DeliveryOutcome,
    ) -> None:
        if (
            outcome.receipt is not None
            and outcome.receipt.status is DeliveryReceiptStatus.RETRYABLE_FAILURE
        ):
            return
        for content in context.message.content:
            if not isinstance(content, AttachmentContent):
                continue
            record = self._records.get(content.attachment_id)
            if record is None:
                continue
            observed = record["observed"]
            expected = record["expected"]
            if not isinstance(observed, int) or not isinstance(expected, int):
                raise RuntimeError("consumer artifact ledger record is malformed")
            record["observed"] = observed + 1
            if observed + 1 >= expected:
                self._remove(content.attachment_id)
            else:
                self._persist()

    def abandon(self, artifact_id: str) -> None:
        """Release one proven-unused lease after cancellation or failure."""

        self._remove(artifact_id)

    def sweep(self) -> int:
        """Crash-safe bounded startup sweep of consumer-owned lease files."""

        entry_limit = (self._max_leases * 2) + 2
        with os.scandir(self._root) as entries:
            bounded_entries = tuple(itertools.islice(entries, entry_limit + 1))
        if len(bounded_entries) > entry_limit:
            raise RuntimeError("consumer artifact sweep directory-entry bound exceeded")
        payload_paths = {
            self._root / entry.name
            for entry in bounded_entries
            if self._OWNED_PAYLOAD.fullmatch(entry.name)
        }
        self._records.clear()
        self._persist()
        for candidate in payload_paths:
            candidate.unlink(missing_ok=True)
        temporary_ledger = self._ledger_path.with_suffix(".tmp")
        temporary_ledger.unlink(missing_ok=True)
        self._fsync_root()
        return len(payload_paths)

    def _load(self) -> dict[str, dict[str, object]]:
        encoded = self._read_ledger_bytes()
        if encoded is None:
            return {}
        try:
            raw = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("consumer artifact ledger is malformed") from error
        if not isinstance(raw, dict) or len(raw) > self._max_leases:
            raise RuntimeError("consumer artifact ledger is malformed or over capacity")
        records: dict[str, dict[str, object]] = {}
        for artifact_id, record in raw.items():
            if (
                not isinstance(artifact_id, str)
                or not artifact_id
                or len(artifact_id) > 128
                or not isinstance(record, dict)
            ):
                raise RuntimeError("consumer artifact ledger record is malformed")
            path = Path(str(record.get("path", "")))
            expected_path = self._root / (
                f"artifact-{hashlib.sha256(artifact_id.encode()).hexdigest()}.txt"
            )
            if path != expected_path:
                raise RuntimeError("consumer artifact ledger path escaped its root")
            expected = record.get("expected")
            observed = record.get("observed")
            size = record.get("size")
            digest = record.get("sha256")
            if (
                not isinstance(expected, int)
                or isinstance(expected, bool)
                or not 1 <= expected <= 64
                or not isinstance(observed, int)
                or isinstance(observed, bool)
                or not 0 <= observed <= expected
                or not isinstance(size, int)
                or isinstance(size, bool)
                or not 1 <= size <= 1_024
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise RuntimeError("consumer artifact ledger record is malformed")
            records[artifact_id] = dict(record)
        return records

    def _read_ledger_bytes(self) -> bytes | None:
        """Read the ledger through one pinned, bounded, no-follow descriptor."""

        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if no_follow is None or directory is None:
            raise RuntimeError("consumer artifact ledger cannot be opened safely")
        root_descriptor = os.open(self._root, os.O_RDONLY | directory | no_follow)
        ledger_descriptor: int | None = None
        try:
            opened_root = os.fstat(root_descriptor)
            current_root = os.stat(self._root, follow_symlinks=False)
            if not stat.S_ISDIR(current_root.st_mode) or (
                opened_root.st_dev,
                opened_root.st_ino,
            ) != (current_root.st_dev, current_root.st_ino):
                raise RuntimeError("consumer artifact ledger root changed during startup")
            try:
                ledger_descriptor = os.open(
                    self._ledger_path.name,
                    os.O_RDONLY | no_follow,
                    dir_fd=root_descriptor,
                )
            except FileNotFoundError:
                try:
                    os.stat(
                        self._ledger_path.name,
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return None
                raise RuntimeError("consumer artifact ledger changed during startup")
            except OSError as error:
                raise RuntimeError(
                    "consumer artifact ledger is not a trusted regular file"
                ) from error
            before = os.fstat(ledger_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("consumer artifact ledger is not a trusted regular file")
            if before.st_size > self._MAX_LEDGER_BYTES:
                raise RuntimeError("consumer artifact ledger exceeds its byte bound")
            chunks: list[bytes] = []
            remaining = self._MAX_LEDGER_BYTES + 1
            while remaining:
                chunk = os.read(ledger_descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            after = os.fstat(ledger_descriptor)
            try:
                current = os.stat(
                    self._ledger_path.name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError as error:
                raise RuntimeError("consumer artifact ledger changed during startup") from error
            if (
                len(encoded) > self._MAX_LEDGER_BYTES
                or len(encoded) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
                or not stat.S_ISREG(current.st_mode)
            ):
                raise RuntimeError("consumer artifact ledger changed during startup")
            return encoded
        finally:
            if ledger_descriptor is not None:
                os.close(ledger_descriptor)
            os.close(root_descriptor)

    def _remove(self, artifact_id: str, *, persist: bool = True) -> None:
        record = self._records.pop(artifact_id, None)
        if persist:
            self._persist()
        if record is not None:
            path = Path(str(record["path"]))
            if path.parent != self._root or not self._OWNED_PAYLOAD.fullmatch(path.name):
                raise RuntimeError("consumer artifact ledger path escaped its root")
            path.unlink(missing_ok=True)
            self._fsync_root()

    def _persist(self) -> None:
        temporary = self._ledger_path.with_suffix(".tmp")
        encoded = json.dumps(self._records, sort_keys=True, separators=(",", ":")).encode()
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    raise OSError("consumer artifact ledger write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._ledger_path)
        self._fsync_root()

    def _fsync_root(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def build_reference_consumer(
    *,
    application: ReferenceApplication | None = None,
    channel: ReferenceChannel | None = None,
    store: GatewayStore | None = None,
    delivery_authorizer: DeliveryAuthorizer | None = None,
    delivery_outcome_observer: DeliveryOutcomeObserver | None = None,
    trusted_attachment_root: str | Path | None = None,
) -> ReferenceConsumer:
    """Build one explicit graph without private SDK seams or global state."""

    if channel is None:
        channel = ReferenceChannel(
            max_outbound_records=128,
            trusted_attachment_root=trusted_attachment_root,
        )
    if application is None:
        application = ReferenceApplication(
            max_projects=2,
            max_threads=4,
            max_turns_per_thread=8,
            max_events_per_thread=64,
        )
    if store is None:
        store = MemoryGatewayStore(
            max_effect_receipts=64,
            max_idempotency_records=128,
            max_delivery_submission_records=128,
        )
    registry = build_command_registry(ReferenceStatusService())
    gateway = Gateway(
        gateway_id="reference",
        channels=[channel],
        applications=[application],
        store=store,
        controller=registry,
        delivery_authorizer=delivery_authorizer,
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        limits=GatewayLimits(
            request_delivery_max_pending=16,
            startup_buffer_max_pending=16,
            turn_acceptance_event_max_pending=16,
            delivery_submission_max_records=128,
            conversation_serialization_max_active_keys=16,
            idempotency_max_records=128,
            projection_max_active_threads=4,
        ),
        extensions=GatewayExtensions(
            request_presenter=MarkdownRequestPresenter(),
            delivery_outcome_observer=delivery_outcome_observer,
        ),
    )
    return ReferenceConsumer(
        channel=channel,
        application=application,
        registry=registry,
        gateway=gateway,
    )


__all__ = ["ReferenceArtifactLedger", "ReferenceConsumer", "build_reference_consumer"]

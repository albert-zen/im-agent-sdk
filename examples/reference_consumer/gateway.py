"""One public, production-shaped composition for the neutral consumer."""

from __future__ import annotations

import hashlib
import json
import os
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

    def __init__(self, root: str | Path, *, max_leases: int = 8) -> None:
        if not isinstance(max_leases, int) or isinstance(max_leases, bool) or max_leases < 1:
            raise ValueError("artifact ledger max_leases must be a positive integer")
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
        path = self._root / f"artifact-{hashlib.sha256(artifact_id.encode()).hexdigest()}.txt"
        temporary = path.with_suffix(".part")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
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
        del outcome
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
        """Crash-safe startup sweep of every leftover consumer-owned lease."""

        artifact_ids = tuple(self._records)
        payload_paths = {Path(str(record["path"])) for record in self._records.values()}
        for artifact_id in artifact_ids:
            self._remove(artifact_id, persist=False)
        for pattern in ("artifact-*.txt", "artifact-*.part"):
            for candidate in self._root.glob(pattern):
                if candidate.exists() or candidate.is_symlink():
                    payload_paths.add(candidate)
                    candidate.unlink(missing_ok=True)
        temporary_ledger = self._ledger_path.with_suffix(".tmp")
        temporary_ledger.unlink(missing_ok=True)
        self._persist()
        return len(payload_paths)

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._ledger_path.exists():
            return {}
        if self._ledger_path.stat().st_size > self._MAX_LEDGER_BYTES:
            raise RuntimeError("consumer artifact ledger exceeds its byte bound")
        raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
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
            path = Path(str(record.get("path", ""))).resolve()
            try:
                path.relative_to(self._root)
            except ValueError as error:
                raise RuntimeError("consumer artifact ledger path escaped its root") from error
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

    def _remove(self, artifact_id: str, *, persist: bool = True) -> None:
        record = self._records.pop(artifact_id, None)
        if record is not None:
            path = Path(str(record["path"])).resolve()
            path.relative_to(self._root)
            path.unlink(missing_ok=True)
        if persist:
            self._persist()

    def _persist(self) -> None:
        temporary = self._ledger_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._records, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self._ledger_path)


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

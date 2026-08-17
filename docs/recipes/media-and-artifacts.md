# Recipe: materialize and deliver media or artifacts

Treat every attachment source as typed location, never as authority. Configure
trust at the accepting adapter, and keep artifact bytes, leases, quota, ledger,
and crash cleanup in the consumer.

## Prerequisites

- An accepting Channel/Application capability that declares the source kind,
  media type, count, size, and grouping it supports.
- An explicit trusted shared root for `LocalPath`; no message or Metadata value
  can grant filesystem trust.
- Consumer policy for `RemoteUrl` scheme/address/redirect/credential limits or
  an explicit handle resolver. Core provides neither.
- For App Server artifact candidates, a replay-safe consumer materializer with
  finite byte, quota, concurrency, lifetime, ledger, and startup-sweep bounds.

## Public API path

```python
from imagent.applications.presentation import (
    ApplicationArtifactMaterialization,
    AppServerArtifactMaterializer,
)
from imagent.interaction.media import AttachmentContent, LocalPath


class ProductArtifactMaterializer(AppServerArtifactMaterializer):
    async def materialize_completed_item(self, facts):
        attachment = await product_artifact_store.materialize(facts)
        return ApplicationArtifactMaterialization(attachments=(attachment,))

    async def materialize_turn_terminal(self, facts):
        return None


# Inject the materializer into the concrete Codex/Zen App Server adapter.
# Configure the same consumer-owned trusted root on the accepting Channel.
attachment = AttachmentContent(
    attachment_id="artifact:item-42:0",
    media_type="image/png",
    source=LocalPath(path=materialized_absolute_path),
    filename="result.png",
    size_bytes=materialized_size,
    metadata={"sha256": materialized_sha256},
)
```

The candidate locator passed to the materializer is untrusted. Validate and
acquire bytes beneath the consumer root, then return only bounded typed
attachments. For proactive `LocalPath` delivery, the lowercase SHA-256 digest
is mandatory and the Channel verifies the same acquired bytes before native
upload. Do not put a path in Metadata or build an SDK spool.

## Owner and authority

Interaction owns typed media sources and shared trust validation. The concrete
Application adapter owns extraction and ordered A1 invocation. The Channel
owns native download/upload and receipts. The consumer owns all bytes and
durable cleanup. See [media design](../components/interaction/media/design.md),
[artifact materialization](../components/applications/presentation/artifact-materialization/design.md),
and [ADR 0003](../decisions/0003-attachment-sources-and-trust.md).

## Typed failure modes

- `UnsupportedGenericFileError` / unsupported source or capability: reject
  before native work.
- `InvalidGenericFileError`, size/count/media/grouping/digest failure, or
  untrusted/outside-root path: reject before upload.
- `ApplicationArtifactMaterializationError`: consumer output violated the
  bounded typed contract.
- `ApplicationArtifactMaterializationCapacityError` or
  `ApplicationArtifactMaterializationTimeout`: the finite A1 runtime could not
  admit or finish work.
- `ApplicationArtifactMaterializationCancelled` or
  `ApplicationArtifactMaterializationFailed`: the affected observation fails
  explicitly and recovers only from authoritative history.
- Retryable delivery retains the consumer lease until explicit retry reaches
  the consumer's terminal rule; unknown delivery is never resent blindly.

## Diagnostics

Application diagnostic facts expose only fixed materialization failure codes
and bounded counters. Gateway diagnostics aggregate those facts without
candidate locator, path, digest, content, or exception text. The consumer must
diagnose byte-store, quota, ledger, and cleanup state in its own observability
system.

## Executable evidence

- `PYTHONPATH=src python -m unittest tests.interaction.test_media -v`
- `PYTHONPATH=src python -m unittest tests.interaction.test_media_staging -v`
- `PYTHONPATH=src python -m unittest tests.applications.presentation.test_artifact_materialization -v`
- `PYTHONPATH=src:. python -m unittest tests.gateway.test_reference_consumer -v`

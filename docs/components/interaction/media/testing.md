# Interaction media testing

## Contract and trust scenarios

Tests for `interaction.media` must prove:

- each attachment source has one stable discriminant and schema/Python shape;
- source kind, media type, declared size/count, grouping, and accepting
  capability limits fail explicitly before unsupported native work;
- generic UTF-8 text and structurally valid PDF bytes map to their declared
  media type, while unsupported suffixes, NUL/non-UTF-8 text, and malformed
  PDF bytes fail explicitly;
- `LocalPath` is rejected without an explicit shared root, when relative, or
  when resolved outside that root, and succeeds only inside the trusted root;
- `RemoteUrl` is never fetched by an unrestricted common downloader and
  `AttachmentHandle` remains unsupported without a resolver;
- attachment locations cannot be smuggled through Metadata;
- durable inbound admission rejects restart duplicates before Channel media
  preparation and releases only the still-owned pre-handoff claim on
  preparation failure;
- proactive inline staging authenticates before decoding, bounds byte count,
  confines generated paths, preserves content order, includes a stable
  digest, and cleans process-local staging after synchronous submission;
- public proactive `LocalPath` content requires lowercase SHA-256 identity and
  native loading rejects changed bytes;
- A1 candidates confer no trust, materialization is bounded and replay-safe,
  and O2 loss cannot turn the SDK into a durable spool or cleanup ledger;
- absence of optional materialization preserves existing adapter behavior.

The value/trust extraction establishes focused evidence at:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.test_media \
  tests.test_contracts \
  tests.test_gateway_vertical_slice -v
```

`tests/interaction/test_media.py` owns exact facade identity, discriminant,
local shared-root trust, and generic-file byte/type validation cases. Proactive ingress, admission, native
Channel I/O, and Application materialization cases remain with their owning
components; they are not moved merely because they consume typed media.

`tests/interaction/test_media_staging.py` owns the pure inline-staging cases:
decoded-byte bounds, declared-size equality, invalid base64, private-directory
confinement, SDK-controlled filenames, stable digest metadata, and source
order. Gateway ingress tests continue to own authorization-before-decode,
synchronous send/cancellation join, cleanup, and route/result mapping. The
client-tools suite owns local artifact encoding and loopback policy.

Native Channel and Application suites remain responsible for their provider
media I/O and materialization behavior. During the mechanical move, shared
source/trust cases move once to `tests/interaction/test_media.py`; owner-
specific admission, proactive-delivery, Channel, and Application cases stay
with those components.

Any schema or public-contract change also requires `validate_schemas.py`,
component-map validation, affected native adapter tests, and the repository-
wide checks in `AGENTS.md`.

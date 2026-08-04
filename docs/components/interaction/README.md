# Interaction components

Interaction is the product-neutral IM boundary. It owns content-bearing
messages, the common control-intent vocabulary, explicit media trust values,
bounded Controller composition, and Channel ingress/delivery contracts. It
does not own Gateway orchestration or native Agent truth.

## Value leaves

- [Messages](messages/design.md) — inbound and outbound content envelopes; see
  [testing](messages/testing.md).
- [Operations](operations/design.md) — the common typed operation result,
  error, and validation vocabulary; see [testing](operations/testing.md).
- [Media](media/design.md) — attachment sources and source/type/size/trust
  boundaries; see [testing](media/testing.md).
- [Diagnostics](diagnostics/design.md) — dependency-neutral immutable
  connection/queue facts; see [testing](diagnostics/testing.md).
- [Client tools](client-tools/design.md) — stateless local argument,
  credential, loopback endpoint, artifact-encoding, and response-presentation
  helpers; see [testing](client-tools/testing.md).
- [Controllers](controllers/README.md) — optional typed Controller contract,
  explicit bounded command composition, common commands, and request
  presentation.
- [Channels](channels/README.md) — lifecycle/admission contracts, verified
  ingress, native outbound evidence, and concrete IM adapters.

`Message` carries content. `Operation` carries control intent. A Controller
may recognize a command in an inbound Message and invoke a typed action, but
the command text does not become message content at an Application boundary
and product commands do not enter SDK Core.

Controller contracts, registry, common commands, and request presentation now
reside under Interaction. `imagent.interaction.controllers` is their sole
formal public facade. The historical `imagent.controllers` package is absent
and intentionally unimportable; no compatibility implementation or alias
remains.

The optional `imagent-send` command resides under
`imagent.interaction.client_tools`. It is a client of consumer-hosted Gateway
ingress, not Gateway orchestration or an Application runtime. The historical
`imagent.cli` package is absent without a compatibility shim.

The adapter conformance implementation is physically mirrored at
`src/imagent/interaction/testing/**`; its repository authority remains the
[engineering testing-and-conformance leaf](../../engineering/testing-and-conformance/design.md).
The mirror does not add a runtime dependency or change Interaction contract
semantics.

See the exhaustive [component map](../component-map.yml) for current and target
code paths, tests, exports, dependencies, accepted decisions, and declared
split candidates. The map, rather than current filenames, defines ownership
during the mechanical migration.

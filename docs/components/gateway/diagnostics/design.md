# Gateway diagnostics component design

This is the authoritative design for the Gateway-owned portion of the ADR
0014 diagnostics surface. The dependency-neutral connection and queue
contracts remain owned by [Interaction diagnostics](../../interaction/diagnostics/design.md),
Channel facts/providers remain owned by [Interaction Channel diagnostics](../../interaction/channels/diagnostics/design.md),
and Application/A1 facts/providers remain owned by [Applications diagnostics](../../applications/diagnostics/design.md).

## Responsibility and exact ownership

`src/imagent/gateway/diagnostics.py` is the sole owner of the retained Gateway
diagnostic implementation:

- `InboundContentTransformFailureCode` and
  `InboundContentTransformerDiagnosticFacts` for I1;
- `InboundFailurePresentationFailureCode` and
  `InboundFailurePresenterDiagnosticFacts` for I2;
- `OutboundPresentationFailureCode` and
  `OutboundPresentationDiagnosticFacts` for O1;
- `DeliveryOutcomeObserverFailureCode` and
  `DeliveryOutcomeObserverDiagnosticFacts` for O2;
- `ProjectionDiagnosticFacts` for bounded projection/recovery observations;
- `GatewayDiagnosticFacts` and `DiagnosticsSnapshot`;
- `summarize_projection_health`, bounded application/channel provider
  collection, fail-closed normalization, and `new_diagnostics_snapshot`.

The owner may depend on the canonical Interaction and Applications diagnostic
contracts. It does not own or import native adapter state, Channel or
Application provider contracts, Agent health truth, consumer exporters,
callbacks, socket-path work, free-form errors, raw payloads, secrets, or a
second provider/runtime/registry.

## Stable public transition surface

`imagent.diagnostics` is a finite, explicit transition facade. It imports and
re-exports the exact objects from the canonical Interaction, Channel,
Applications, and Gateway owners. It contains no duplicate definitions and no
lazy `__getattr__` or dynamic lookup. The `imagent.gateway` package facade
continues to expose its existing Gateway diagnostics names by exact identity.
Import-order and `get_type_hints` identity evidence is maintained in
`tests/gateway/test_diagnostics.py` and the clean-wheel smoke.

The facade is a compatibility boundary only; Gateway internals import
`imagent.gateway.diagnostics` directly, and lower Interaction/Application
modules never import the top-level facade or Gateway.

## Snapshot semantics

Gateway aggregation is synchronous, side-effect-free, and performs no native
or repository I/O. Facts are immutable, redacted, and bounded. Provider
absence, raising, invalid output, or identity mismatch fails closed to the
configured identity without fabricated state. Missing optional I1, I2, O1, or
O2 providers remain absent rather than becoming zero-valued facts.

Failure values use fixed enums. Projection gap strings are normalized to the
accepted finite vocabulary or `other`; no free-form exception, gap, route,
Thread, Turn, request, message, content, credential, endpoint, path, or native
resource value is retained. Queue names and capacities remain bounded, and
queue scope is validated by the canonical lower-layer contracts.

`DiagnosticsSnapshot` remains schema version 8 and non-authoritative.
`generated_at` is observation time only. Repeated reads do not mutate counters,
subscribe, reconnect, admit, dispatch, publish, or call consumer code.

The I1, I2, O1, O2, projection, startup-admission, and lifecycle evidence is
described in the affected [Gateway leaf docs](../README.md), including
[content transformation](../input/content-transformation/design.md),
[failure presentation](../input/failure-presentation/design.md),
[presentation](../presentation/design.md),
[outcome observation](../delivery/outcome-observation/design.md),
[lifecycle](../lifecycle/design.md), and the
[projection subtree](../projection/README.md).

The machine-readable [component map](../../component-map.yml) records the
current transition paths, the target owner, dependencies, exports, and the
explicit formal facade declaration for this completed move.

## Decisions and non-goals

This leaf implements ADR [0014](../../../decisions/0014-read-only-diagnostics-surface.md)
and the typed ownership/seam constraints in
[0015](../../../decisions/0015-typed-extension-seams-and-composition.md).
It does not redesign Gateway, move lower-layer contracts or native state,
change failure/cardinality/capacity semantics, add generic hooks or `Any`, or
turn diagnostics into an authority, exporter, service locator, or runtime.

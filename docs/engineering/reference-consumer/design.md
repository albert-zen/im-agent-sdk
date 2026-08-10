# Reference consumer design

## Purpose

The reference consumer is the repository's one executable acceptance consumer
for the public v1 SDK. It continuously proves that a clean-installed downstream
composition can create explicit managed resources, bind and switch two IM
Conversations, send ordinary input, observe authoritative output once, inspect
bounded diagnostics, and shut down without owned work remaining.

It is acceptance code, not a second Gateway, Agent runtime, transcript,
repository implementation, product starter, or test-only bypass. Runtime
semantics remain authoritative in `docs/components/`; the complete accepted
scenario remains normative in `docs/V1_EXECUTABLE_SPEC.md`.

## Ownership and boundary

This leaf owns:

- the four product-neutral modules under `examples/reference_consumer/`;
- the deterministic executable scenario and its bounded summary;
- the focused integration test at `tests/gateway/test_reference_consumer.py`;
- reference-consumer onboarding under `docs/onboarding/`; and
- source-tree and clean-wheel execution of the same entry point.

It does not own public SDK contracts, Gateway persistence or effect execution,
native adapter policy, product permissions, credentials, presentation policy,
artifact storage, downstream migration, or release publication.

The example imports only installed public SDK surfaces. It never imports a
private Gateway runtime, effect executor, adapter implementation, store session,
repository, claim, checkpoint, or test fixture. The local deterministic Channel
and managed Application implement public ports exactly as downstream adapters
do; their counters may be inspected by the focused test, but the scenario may
not call a fake resource mutation or native event emitter to make the flow pass.

## Canonical flow

The entry point constructs one explicit managed-CWD Application, one Channel,
one coherent `MemoryGatewayStore`, one frozen local command registry, and one
`Gateway`. While the Gateway context is running it uses only scoped public
actions and Channel ingress to perform the golden path. Conversation A first
discovers and reads the Application through both public action factories, then
creates its Project and Thread and completes a single-destination ordinary
round trip before Conversation B binds for shared fan-out. Non-command text
passes through the optional Controller and then follows D's single policy-free
ordinary-input resolver and dispatcher; the example supplies no alternate
dispatch hook. The full scenario is defined in
`docs/V1_EXECUTABLE_SPEC.md`.

Stable action, resource, message, event, and delivery identities drive every
assertion. Output isolation is proved by exact destination sets across shared
Thread, switch, and switch-back phases. At most one Application subscription is
active for each stable Thread. The printed result contains only fixed labels,
counts, and Booleans; it never emits content, CWDs, Conversation/Thread IDs,
credentials, endpoints, or exception text.

## Bounds and lifecycle

Every local collection and subscription has a positive finite bound. Capacity
failure occurs before mutation or append. The managed CWD is supplied
explicitly by the caller and is never a default or onboarding inference.

The Gateway async context owns startup and shutdown. The final scenario checks
that the Channel and Application are stopped, Application subscriptions are
closed, registry work is joined, the Gateway store lease/resources are closed,
and no scenario-owned task remains. Diagnostics are read synchronously while
running and validated only as bounded, redacted, non-authoritative facts.

## Packaging

The wheel includes `examples/reference_consumer/` so an isolated base install
can run `python -m examples.reference_consumer.main`. Packaging owns inclusion
and isolated installation; this leaf owns what that entry point proves. There
is no second example, compatibility entry point, or source-tree-only import
path.

## Authority

- [v1 design](../../V1_DESIGN.md)
- [v1 executable specification](../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [release design](../release/design.md)

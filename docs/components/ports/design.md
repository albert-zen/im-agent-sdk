# Python ports component design

## Purpose

`src/imagent/adapters.py` is the remaining compatibility facade for Python
runtime seams that have not yet reached their target component owner. New and
mechanically extracted contracts live under the owning Interaction, Gateway,
or Applications component; the facade may re-export an exact object while
callers migrate, but it does not retain a second implementation.

## Ownership

The remaining Ports surface owns:

- `ChannelAdapter` lifecycle, inbound callback, and send signatures;
- `AgentApplicationAdapter` lifecycle, typed operation, input, and Thread
  subscription signatures;
- the common input continuation preference, typed pre-dispatch intent, and
  truthful started/steered acceptance result;
- `ApplicationInputOutcomeUnknown`, which marks a dispatched native input
  whose acceptance result cannot be proven and therefore cannot be retried
  automatically;
- `BindingRepository`, `ProjectionRouteRepository`, and
  `IdempotencyRepository` interfaces;
- `DeliveryAuthorizer` and `DeliverySubmissionRepository` interfaces for
  scoped proactive delivery, immutable route snapshots, and typed outcomes;
- callback aliases that have not yet moved to their owner-typed seam.

Interaction's Channel contract owns `MessageHandler`, the optional structural
`ChannelStartupConfigurationValidator`, and the opaque `InboundAdmission`
lease and handler used before Channel media preparation. `adapters.py`
re-exports those exact objects for compatibility. `ChannelAdapter` and the
remaining repository/Application Ports stay here until focused mechanical
owner moves. The historical `OperationHandler[GatewayOperation]` is removed:
Channel lifecycle accepts messages/admission only, while Controllers invoke
typed operations through `ControllerActions`.

It does not own:

- resource/message/operation semantics or JSON Schema;
- native Channel or Agent Application behavior;
- repository implementations or storage format;
- Gateway orchestration, recovery policy, or product UX.

## Dependency direction

Each extracted seam imports only lower-layer contracts owned by its component.
The remaining Ports surface imports Contracts and may import an owning leaf
solely for an exact compatibility re-export. Gateway, persistence
implementations, recovery, test kits, and concrete integrations depend on the
owning component where dependency-safe; Contracts never depend on Ports.

Adding a method requires a real caller and at least one implementation. A
native-specific method stays on a concrete adapter until at least two
integrations prove a common port.

QQ, Telegram, Feishu, and Weixin prove the Interaction-owned
startup-validator structure. It
contains only synchronous `validate_startup_configuration()` and is
runtime-checkable for operator composition. It does not make validation
mandatory for `ChannelAdapter`, expose resolved credentials/configuration, or
carry Gateway, diagnostics, and message-extension authority. Absence means the
Channel has no SDK startup validator; it does not mean validation succeeded.

ADR 0014 Channel diagnostics is another optional structural provider, defined
by the diagnostics component rather than added to `ChannelAdapter`. Its sync
read returns immutable bounded facts only. Capability absence remains valid;
provider failure is handled by snapshot collection and never by Channel input
or delivery paths.

ADR 0015 extension protocols are stage-specific Ports only when their issue
has a real consumer and default counterexample. They carry minimum immutable
typed inputs and never Gateway, repositories, a mutable context bag, or a
generic stage discriminant. I1 is replay-safe before native dispatch; O2 is a
best-effort process-local observation rather than a durable subscription.

The Codex and T3 non-artifact A1 presenter protocols remain concrete
Application-adapter configuration, not methods on `AgentApplicationAdapter`.
Their native fact shapes differ, while both return the same bounded text-only
presentation value. Zen and an adapter with no activity presenter remain valid
without optional duck typing on the common Port.

`ProjectionRouteRepository` owns explicit merge/advance and Turn-correlation
operations because these are common Gateway projection state across
Application and Channel implementations. It does not expose transcript or
native Turn mutation APIs. Route refresh preserves an omitted checkpoint and
rejects a conflicting explicit value. Checkpoint advance is compare-and-swap
against an expected opaque Agent item ID; implementations never infer ordering
from that ID. Correlation bulk deletion requires at least one explicit
selector.

`DeliveryAuthorizer.authenticate` converts an opaque untrusted credential into
a trusted `DeliveryPrincipal`; the caller cannot declare its own effective
scope. `DeliverySubmissionRepository.reserve_delivery_submission` is atomic
and stores identity/snapshots/outcomes only. It is intentionally not a queue,
content store, or retry scheduler.

Application input failures must preserve their side-effect boundary. A
definitive pre-dispatch rejection may be retried by the caller; once dispatch
has begun, cancellation, timeout, disconnect, or response loss is reported as
`ApplicationInputOutcomeUnknown` unless the native protocol supplies a
stronger idempotency guarantee.

`AgentApplicationAdapter.send_input` defaults to `prefer_active_turn`. Every
implementation accepts that preference even if its evidenced native mapping
can only return `started`. Immediately before mutation it calls the supplied
dispatch hook with `started/create_new` or
`steered/preserve_existing(expected_turn_id)`. This is a common Port because
Codex continuation and the T3/Zen start paths all need the same Gateway
correlation boundary; the Port does not expose a native `steer_turn` method.

`IdempotencyRepository.mark_side_effect_started` durably separates a
reclaimable lease from work that may already have changed a remote system.
Implementations must never age the protected state back into permission to
retry. A caller that supplies an owner token on acquisition must reuse it as a
fencing token for every later state mutation.

The Interaction-owned `InboundAdmissionHandler` carries only stable
Conversation/message identity.
It returns a one-shot opaque lease or no lease for duplicate/in-flight work.
Channel adapters release only preparation failures before handoff; after
`InboundAdmission.deliver`, Gateway owns the terminal transition.
`IdempotencyRepository.refresh` fences the handoff by updating only the owned
`in_flight` timestamp.

Gateway preserves the legacy message-only `ChannelAdapter.start(on_message)`
shape during migration by inspecting its signature before invocation. Such
adapters retain late Gateway idempotency but cannot claim the pre-media
guarantee. A modern Channel accepts
`start(on_message, on_admission=None)` and must use a returned lease for media
work. Gateway never retries a partially started adapter with another shape.

## Change obligations

Changes to a Port require checking all implementations, fakes, type checking,
contract suites, and any component docs whose call flow changes. A method
signature change is a public Python API change even when JSON Schema is
unchanged.

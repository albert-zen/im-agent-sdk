# Gateway inbound admission design

Component ID: `gateway.admission`

Parent: `gateway`

## Purpose

Admission acquires durable, fenced identity before a Channel downloads or
prepares media and transfers exactly one owned claim to Gateway processing.

## Ownership and contract

This leaf owns `InboundAdmissionService`, `ClaimedInbound`, the stable inbound
idempotency identity, lease refresh/release, and handoff fencing. It does not
own Channel authorization, content bytes, media trust, Conversation routing,
Controller policy, native input dispatch, or idempotency persistence itself.

Inputs are a Channel instance ID, `ConversationRef`, and stable native message
ID. `begin()` verifies the Channel identity and claims:

```text
scope = inbound:<channel-instance-id>
key   = <native-conversation-id>:<native-message-id>
```

Each acquisition has an opaque owner token. A duplicate or protected claim
returns no lease. Before handoff, the one-shot lease verifies that the prepared
`InboundMessage` has the same Conversation and message ID, refreshes the exact
owned claim, then transfers a `ClaimedInbound`. Failed preparation or a
pre-handoff fencing failure releases only that owner; a stale owner cannot
release or use a replacement claim.

The Channel contract offers admission before proportional media preparation.
`start_channel_with_admission` currently retains the documented signature
migration fallback for an older Channel implementation. That fallback cannot
move admission ahead of legacy adapter preparation, but it still enters the
same Gateway claim authority rather than creating a second authorization path.
It remains an explicit removal gap; supported Channels must converge on the
pre-media admission callback.

## State and recovery

Lease state is finite and process-local (`open`, refreshing/releasing, then
transferred/released/closed). Durable authority is solely the idempotency
repository: ordinary `in_flight` may be reclaimed only under its owner/lease
rules, while `side_effect_started` never expires into permission for another
native input. Admission never persists content or creates a media spool.

## Structure and dependencies

`src/imagent/gateway/admission.py` is the sole implementation owner. The
live/startup gate in `src/imagent/gateway/__init__.py` composes that owner
without duplicating admission behavior. `InboundAdmissionService` and
`ClaimedInbound` are exported directly from `imagent.gateway.admission` and
re-exported by `imagent.gateway` with exact object identity. The historical
`imagent.inbound_admission` module is absent; it is not a compatibility shim.

Dependencies are the Interaction Channel/message contracts and Gateway
idempotency repository contract/implementation. No Application or concrete
Channel adapter dependency is allowed.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Persistence design](../../persistence/design.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0011](../../../decisions/0011-durable-inbound-admission-before-media.md)

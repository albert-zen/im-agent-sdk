# T3 Application adapter design

Component ID: `applications.adapters.t3`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the T3 concrete Application adapter and its authenticated HTTP
client: native Project/Thread discovery, Thread/Turn reads, input dispatch,
stable native message/activity normalization, one finite per-Thread polling
flow, canonical event publication, and authoritative history/catch-up.

It does not own a synthetic App Server connection or epoch, interactive
request response surface, Gateway binding/delivery/persistence, product
model/provider policy, raw native events, or a second polling/subscription
runtime. Native T3 remains the authority for resources and execution.

## Typed boundary and public facade

Inputs are `HttpT3Client` HTTP request values, bearer-token configuration,
common Application operations/inputs, and native JSON resource responses.
Outputs are `ApplicationSummary`, typed operation/results, native acceptance
receipts, canonical `AgentEvent` values, and optional bounded recoverable
`T3ActivityFacts` presentation. `T3ClientError` is the typed HTTP failure.
Interactive requests remain an explicit unsupported capability.

The current formal exports are `T3ApplicationAdapter`, `HttpT3Client`, and
`T3ClientError` through `imagent.applications`; the adapter and client live in
`src/imagent/applications/t3.py` and `t3_client.py`. The target exact facade is
`imagent.applications.adapters.t3` with the same objects and no duplicate HTTP
client.

## Dependencies, state, and recovery

T3 depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, and the live-activity
presentation leaf. Stable native IDs/history and the optional A1
cursor/presentation window are finite. The current adapter still retains
`_turn_baselines`, `_send_locks`, `_seen_messages`, and `_terminal_turns`
without an explicit capacity/eviction bound; finite poll/dedupe retention is a
real implementation gap. A missing cursor or polling gap ends the affected
subscription with explicit recoverable gap semantics; history/catch-up may
recover the association. Configured T3 input returns native acceptance before
optional presenter work. There is no long-lived connection diagnostic or
synthetic connection epoch.

## Current, target, and structural gap

Current code is `src/imagent/applications/t3.py` plus
`src/imagent/applications/t3_client.py`. Current evidence is
`tests/test_t3_client.py`, `tests/conformance/test_adapter_contracts.py`,
`tests/test_gateway_vertical_slice.py`, and the live-activity presentation
suite. The target is `src/imagent/applications/adapters/t3.py` with focused
tests at `tests/applications/adapters/test_t3.py`. The gap is that `t3.py`
currently combines orchestration, mapping, polling, presentation invocation,
and attachment encoding; later mechanical slices must preserve one ordered
polling state machine.

## Authority

- [Adapter block](../README.md)
- [T3 transition page](../../../application-adapters/adapters/t3.md)
- [Applications adapter overview](../../../application-adapters/design.md)
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

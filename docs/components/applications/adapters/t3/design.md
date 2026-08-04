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

The stable formal exports are `T3ApplicationAdapter`, `HttpT3Client`, and
`T3ClientError` through `imagent.applications`. Their one implementation owner
is `imagent.applications.adapters.t3`, backed by the single source file
`src/imagent/applications/adapters/t3.py`. The historical `t3.py` and
`t3_client.py` modules are absent; the package facade resolves the exact
objects from the target module and does not retain an internal import shim or
duplicate HTTP client.

## Dependencies, state, and recovery

T3 depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, and the live-activity
presentation leaf. Stable native IDs/history and all process-local poll state
are finite. The adapter's four explicit state capacities default to 4096
turn-baseline entries, 1024 concurrently held Thread send locks, 8192 seen
message identities, and 4096 terminal-Turn identities. Baselines are keyed by
`(native_thread_id, native_turn_id)` and reserve capacity before the typed
pre-dispatch callback; only baselines already observed terminal may be
evicted, oldest first. If every baseline is active, input fails before native
mutation rather than dropping active authority. Send-lock entries are retained
only while a Thread has a waiter or owner and reject a new Thread when the
active-key capacity is full. Seen-message and terminal-Turn windows use stable
`(native_thread_id, native_id)` keys and deterministic oldest-first eviction;
eviction can cause replay from native history but never changes native truth or
creates a local transcript/spool.

A missing cursor or polling gap ends the affected subscription with explicit
recoverable gap semantics; history/catch-up may recover the association.
T3 input performs all validation, baseline reading, command construction, and
capacity admission before the typed pre-dispatch callback. If that callback
raises, no native dispatch is attempted. Once the single native dispatch is
attempted, every ordinary exception or cancellation from dispatch or the
authoritative follow-up Thread read—including a missing Turn ID—is raised as
`ApplicationInputOutcomeUnknown` with the original cause; an existing unknown
outcome is not wrapped again. The adapter never retries or falls back to a
second dispatch and releases only its process-local baseline reservation.
After a stable Turn ID is recorded, `send_input` returns `AcceptedTurn`
immediately. It does not synchronously publish or invoke presentation; the
existing sole subscription poll/history path performs recoverable observation.
There is no long-lived connection diagnostic or synthetic connection epoch.

## Current, target, and structural gap

The implementation and focused owner evidence are now co-located at
`src/imagent/applications/adapters/t3.py` and
`tests/applications/adapters/test_t3.py`. The module intentionally keeps the
HTTP client, adapter orchestration, mapping, polling, presentation invocation,
and attachment encoding in one T3 owner; this slice does not introduce a
second runtime or a generic internal plugin boundary. The remaining state
rules are the finite capacities and explicit active-authority behavior above.

## Authority

- [Adapter block](../README.md)
- [T3 transition page](../../../application-adapters/adapters/t3.md)
- [Applications adapter overview](../../../application-adapters/design.md)
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

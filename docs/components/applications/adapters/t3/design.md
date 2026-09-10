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
`T3ClientError` through the canonical `imagent.applications.adapters.t3`
facade. Their one implementation owner is `imagent.applications.adapters.t3`,
backed by the single source file
`src/imagent/applications/adapters/t3.py`. The historical `t3.py` and
`t3_client.py` modules and the `imagent.applications` root aliases are
absent; the canonical facade resolves the exact objects from the target
module and does not retain an internal import shim or duplicate HTTP client.

## Dependencies, state, and recovery

T3 depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, the direct canonical
`applications.diagnostics` owner for Application diagnostic facts, and the
live-activity presentation leaf. Stable native IDs/history and all
process-local poll state are finite. The adapter's four explicit state
capacities default to 4096
turn-baseline entries, 1024 concurrently held Thread send locks, 8192 seen
message identities, and 4096 terminal-Turn identities. Baselines are keyed by
`TurnRef` and reserve capacity before the typed
pre-dispatch callback; only baselines already observed terminal may be
evicted, oldest first. If every baseline is active, input fails before native
mutation rather than dropping active authority. Send-lock entries are retained
only while a Thread has a waiter or owner and reject a new Thread when the
active-key capacity is full. Seen-message and terminal-Turn windows use stable
`(ThreadRef, native_id)` keys and deterministic oldest-first eviction;
eviction can cause replay from native history but never changes native truth or
creates a local transcript/spool.

Every mapped T3 Thread must carry a stable native Project ID; a malformed
Project-less Thread is rejected rather than omitted or normalized. T3 keeps
native Project list/read capability, while CWD Project creation remains typed
unsupported because the evidenced client has no such mutation.
Every assistant message that can enter live observation must likewise carry a
stable native Turn ID. Missing Turn ancestry terminates the affected poll with
the fixed native-mapping recovery gap before the message is marked seen or a
canonical event is published.
Every supplied `ThreadRef` is also preflighted against an authoritative native
Thread detail `(projectId, id)` before a read projection, input, polling, or
Thread/Turn mutation. A missing or mismatched native identity fails closed;
same-Application ancestry alone is insufficient and no caller-supplied Project
may key state or events until native scope matches.

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

T3 outbound-projection `AgentMessage` Metadata contains only bounded
non-secret scalar facts: current keys are `kind`, `native_application`,
`source`, and `streaming`; messages carry `native_application` and
`streaming`, while activity projections add the native activity `kind` and
the fixed `source` marker. Native Thread/Turn/message/activity IDs remain in
canonical fields, not Metadata. Recoverable activity projection fixes the
stable activity identity as `AgentMessage.agent_item_id`: the native activity
ID when no presenter is configured, and the namespaced
`imagent:t3-activity:{activity_id}` form when one is, so polling, catch-up,
and authoritative history reproduce the same association.

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
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

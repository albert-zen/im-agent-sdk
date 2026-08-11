# Gateway projection recovery design

Component ID: `gateway.projection.recovery`

Parent: `gateway.projection`

## Purpose and ownership

This leaf reconciles an observation gap using only bounded authoritative
Application history, catch-up, and declared replay capabilities. It owns
recovery-mode selection, bounded page/item scans, gap classification,
resubscription policy inputs, and ordered baseline/recovery facts supplied to
projection routes.

It does not own the Agent history, native cursor semantics, a synthetic replay
journal, transcript persistence, unbounded archive scan, request authority, or
Channel delivery retry. Applications declare what their cursor/history API can
honestly preserve; Gateway does not invent sequence or replay support.

## Recovery modes and ordering

For a replay-capable Application, Gateway subscribes after the opaque native
cursor, consumes ordered native events, surfaces expiry/gap explicitly, and
reconciles authoritative history when required. When replay is absent, Gateway
installs the live subscription first, reads bounded authoritative history or
catch-up, reconciles stable item IDs, then drains live observations through the
route bootstrap barrier. This preserves baseline-before-live delivery without
blocking the native producer.

New routes use one configured recent history page plus bounded active-Turn
catch-up. Existing routes scan newest authoritative pages toward their own
opaque completion checkpoint. All page, item, and active-Turn limits are
positive non-boolean integers. Supervisor retry bounds are finite,
non-negative numeric values, use capped exponential growth, and reject invalid
configuration explicitly. Missing/expired checkpoints, page exhaustion,
unavailable history, or an unsupported recovery capability remain explicit
gap/degraded facts; none licenses a complete archive scan or a claim that
output was fully recovered.

## Failure domains and request honesty

Subscription and recovery failures are per-Thread worker infrastructure facts
that use bounded supervisor backoff. Only a typed Channel/destination decision
failure is isolated to its route and does not restart the Application
subscription, including when the destination reports a safe retry hint.
Recovery never consumes that hint. Correlation-repository reads,
delivery-idempotency or submission infrastructure, and checkpoint repository
CAS remain outside that destination boundary: their failures propagate to the
existing affected-Thread supervisor for authoritative convergence.

One Thread's gap cannot cross another Thread's dispatch-owned acceptance-order
boundary. Recovery observes a gate overflow or failed ordered drain as a typed
external `EventStreamGap` injected into that same supervisor; it neither drains
the gate nor redispatches the accepted input. The supervisor alone classifies
the gap, schedules capped backoff, and requires authoritative reconciliation.
A live worker catches the targeted cancellation and continues as that same
worker; without a live worker, the newly started worker consumes the marker
before recovery. Both paths clear a terminal marker rather than leaving stale
recovery state.

Completed messages reconcile through their stable Application item IDs and
route-scoped idempotency. This leaf supplies only bounded ordered authoritative
evidence to the checkpoint owner, which decides whether completed evidence can
converge the expected-current CAS. Live-only A1 activity is not in
authoritative history and is allowed to be lost across overflow/restart.
Interactive requests require a separate authoritative pending-request snapshot
capability: on a gap, only the affected Thread can reconcile that snapshot; in
its absence Gateway reports request-recovery degradation rather than inventing
a prompt or open request from bridge state.

## Runtime supervision and typed collaboration

The recovery mode/value definitions, bounded authoritative-read helpers,
route-reconciliation orchestration, and private recovery supervisor live in
`gateway/projection/recovery.py`. The supervisor owns only per-worker recovery
attempt state and produces typed retry delay and degraded-health facts.
`ProjectedAgentMessage` and `AuthoritativeProjectionSlice` are its bounded
typed recovery facts. The observation owner imports the projected-message fact,
opens, consumes, closes, and resubscribes the one Application Thread
subscription; it uses the supervisor's bounded inputs and does not create a
second worker or event stream.

Recovery invokes route ordering through a narrow typed collaborator. The route
coordinator physically retains its route lock, the single observation-owned
bootstrap fence, current-route refresh, Channel-delivery isolation, and
checkpoint-facing delivery call pending the accepted move, but it no longer
selects history pages, stores recovery limits, classifies gaps, or supervises
retries. Its catch boundary accepts only the typed destination decision; it
never catches across correlation reads or checkpoint persistence.
For scoped action reconciliation only, observation also supplies a synchronous
process-local lifecycle validator to this private collaboration. Recovery
checks it around every authoritative read, acceptance wait, and delivery
suspension; it carries no repository or mutation authority. Lifecycle or worker
loss propagates before recovery completes the bootstrap fence, allowing the
action owner to classify durable success as partial and later replay the same
route after restart.
Recovery likewise invokes the canonical
request-correlation owner's Thread-scoped pending-snapshot method through its
typed call boundary; it receives only the degraded result and never reads or
mutates request correlations itself.

The `imagent.gateway.projection` facade re-exports `ThreadRecovery`,
`RecoveryMode`, and `ProjectionRecoveryUnavailable` as the exact owner
objects. The historical `imagent.recovery` module is absent; it is not a
compatibility import path.

- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

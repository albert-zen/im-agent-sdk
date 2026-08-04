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
finite. Missing/expired checkpoints, page exhaustion, unavailable history, or
an unsupported recovery capability remain explicit gap/degraded facts; none
licenses a complete archive scan or a claim that output was fully recovered.

## Failure domains and request honesty

Subscription and recovery failures are per-Thread worker infrastructure facts
that use bounded supervisor backoff. A per-route Channel delivery failure is
not a recovery trigger and does not restart the Application subscription. One
Thread's gap cannot cross another Thread's acceptance-order boundary.

Completed messages reconcile through their stable Application item IDs,
route-scoped idempotency, and checkpoint CAS. Live-only A1 activity is not in
authoritative history and is allowed to be lost across overflow/restart.
Interactive requests require a separate authoritative pending-request snapshot
capability: on a gap, only the affected Thread can reconcile that snapshot; in
its absence Gateway reports request-recovery degradation rather than inventing
a prompt or open request from bridge state.

## Current structure and authority

The recovery mode/value definitions and bounded authoritative-read helpers live
in `gateway/projection/recovery.py`. The
`imagent.gateway.projection` facade re-exports `ThreadRecovery`,
`RecoveryMode`, and `ProjectionRecoveryUnavailable` as the exact owner
objects. The historical `imagent.recovery` module is absent; it is not a
compatibility import path.

Recovery supervision remains distributed across `projection_runtime.py`,
`projection_routes.py`, and `request_projection_runtime.py`. Those separate
observation, routing, and request-correlation responsibilities remain outside
this mechanical move.

- [ADR 0004](../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0007](../../../../decisions/0007-projection-lifecycle-and-delivery-boundaries.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

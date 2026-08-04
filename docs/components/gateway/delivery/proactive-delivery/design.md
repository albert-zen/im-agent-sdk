# Gateway proactive delivery design

## Purpose and boundary

`gateway.delivery.proactive-delivery` accepts a typed `DeliveryIntent`,
authenticates its opaque credential, resolves permitted destinations once,
reserves immutable submission snapshots, and sends each destination through
the same planner, bounded coordinator, and optional O2 observer used by other
Gateway output. It owns orchestration and typed results, not native Channel
encoding, product authentication policy, Agent execution, or a second
delivery runtime.

This leaf has an intentional same-leaf physical split:

- `proactive.py` is the low-dependency proactive contract seam and the sole
  implementation owner of the proactive vocabulary and validator. It imports
  only the passive submission state and shared validation helpers owned by
  `gateway.persistence.state_contracts`; the historical module has no proactive definition
  or compatibility alias. Its explicit finite `__all__` contains exactly the
  eight owned vocabulary/validator names; support imports retained for runtime
  type-hint resolution are not part of the public seam.
- `proactive_runtime.py` owns `ResolveThreadRoutes`, `DeliveryRouteError`,
  `ProactiveDeliveryService`, and the private orchestration helpers used only
  by that service. It imports the contract seam and implementation Ports, but
  the contract seam never imports the runtime. This removes the direct
  same-leaf reverse edge needed by the later vocabulary move; it does not make
  an eager historical package facade safe to import from the passive state leaf.

`proactive.py` intentionally does not import or re-export runtime
classes/helpers. The finite `imagent.gateway.delivery` and `imagent.gateway`
facades import runtime objects directly from `proactive_runtime`; proactive
ingress imports `DeliveryRouteError` from that same owner. This keeps one
runtime owner and avoids lazy or function-local reverse imports. Because
Python initializes those package facades before a leaf import, importing the
seam through its public path still initializes the current runtime facade; no
lower component may therefore add a reverse compatibility import to this leaf.
This is an ownership-only move: route resolution, authorization, planning,
coordination, submission identity, replay, receipt, capacity, cancellation,
and failure semantics remain unchanged.

Authorization completes before route resolution. Full intent/capability
preflight completes before any Channel side effect. The first atomic
reservation pins the destination set and payload/target fingerprints; replay
never follows a moved route. External and Gateway-internal identities are
namespaced by fixed origin plus trusted principal.

An existing stable submission is loaded before current route resolution, so a
retry uses its authoritative pinned snapshots even if routing has moved. If
two callers both observe no record and race after resolving different route
snapshots, the repository's complete reservation-identity comparison permits
only the first set and rejects the other; orchestration verifies the same rule
on every non-acquired reservation result.

## Outcome and retry safety

One destination is submitted as one logical Coordinator attempt even when it
has several segments or internal retries. Accepted and unknown native outcomes
are never automatically repeated. Only a persisted explicit retryable receipt
can authorize a later attempt, and its retry-after bound is honored. Partial
or rejected results remain typed and per destination. O2 observes the logical
attempt best-effort and cannot change the receipt or retry decision.

Submission persistence stores identity, fingerprints, route snapshots,
receipts, bounded errors, and timestamps only. It stores no content, artifact
bytes/path lifetime, durable work item, credential, or Agent transcript. The
process-local repository lives in its `gateway.persistence.memory` owner;
proactive orchestration depends only on the repository contract.

## Ingress boundary

The transport-neutral JSON handler now lives with its Gateway proactive-
delivery owner in `gateway.delivery.proactive_ingress`; the reference CLI
remains a separate client for a later focused move. The handler imports the
bounded encoded-artifact staging mechanics from `interaction.media`, but
continues to own JSON parsing, authorization-before-decode, the synchronous
attempt lifetime, cancellation join, cleanup, delivery, and result mapping.
Caller-supplied server paths remain unsupported. The staging helper cannot
authorize, send, retain bytes beyond that attempt, or create a web server,
spool, background worker, or retry scheduler.

The handler also owns one finite process-local delivery-ID coordination
registry. Its keyword-only `max_active_delivery_ids` bound is a positive
non-boolean integer and defaults to 256. The handler first validates the JSON
`deliveryId` as a non-empty string, then uses that exact string without
trimming or namespacing as the registry key. It acquires that key before
target parsing, authentication, base64 inspection/decoding, staging, route
resolution, submission lookup/reservation, or delivery.

At the bound, an already active identical delivery ID joins the existing key
and therefore retains the durable replay/in-flight result of the one
serialized submission. A distinct ID receives a fixed redacted 503 response
with code `delivery_ingress_capacity_exhausted`; the durable repository's
separate `delivery_capacity_exhausted` response remains unchanged. The final
owner/waiter leaving normally or by cancellation removes only that active key,
so a later distinct ID may use the slot. A new handler/restart begins with an
empty registry. The registry is not a durable replay authority and adds no
Gateway limit, global registry, queue, diagnostic provider, hook, worker,
spool, outbox, persistence, or second execution path. Durable submission
snapshots and accepted/retryable/rejected/unknown outcomes remain the sole
restart and retry evidence.

## Public surface

`imagent.gateway.delivery` is the finite target facade for the service and
JSON handler plus typed intent/target/result vocabulary. The historical
`imagent.delivery_ingress` implementation path is removed rather than retained
as a compatibility module. The `imagent.contracts` facade no longer exports
the eight proactive vocabulary/validator names; callers use
`imagent.gateway.delivery` (or its `proactive` owner seam) instead. This is a
removal, not a reverse compatibility alias. The old
`imagent.proactive_delivery` implementation path is removed.

This ownership slice moves only the typed proactive vocabulary and
`validate_delivery_intent` into this seam. It leaves
`DeliverySubmissionOrigin`, `DeliverySubmissionState`, submission records and
reservations, `_canonical_metadata`, `_validate_conversation_ref`, the four
submission identity helpers, authorization, JSON ingress, planning,
coordination, O2, CLI, and persistence in their accepted owners. The
`gateway.persistence.state_contracts` dependency is one-way: proactive delivery
may consume passive state/helpers, but the state leaf never imports delivery
orchestration.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Gateway design](../../design.md)
- [Submissions design](../submissions/design.md)

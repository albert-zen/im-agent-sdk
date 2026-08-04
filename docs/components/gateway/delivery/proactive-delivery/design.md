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

- `proactive.py` is the low-dependency proactive contract seam. In this
  predecessor it still exposes the vocabulary and validator through their
  current `contracts.delivery` objects. The target component map removes the
  historical `imagent.contracts` proactive exports. A separate public-facade
  convergence slice must retire those exports and rewire their internal users
  before #216 moves the definitions and validation here.
- `proactive_runtime.py` owns `ResolveThreadRoutes`, `DeliveryRouteError`,
  `ProactiveDeliveryService`, and the private orchestration helpers used only
  by that service. It imports the contract seam and implementation Ports, but
  the contract seam never imports the runtime. This removes the direct
  same-leaf reverse edge needed by the later vocabulary move; it does not make
  an eager historical package facade safe to import from `contracts.delivery`.

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

## Public surface

`imagent.gateway.delivery` is the finite target facade for the service and
JSON handler plus typed intent/target/result vocabulary. The historical
`imagent.delivery_ingress` implementation path is removed rather than retained
as a compatibility module. `imagent.contracts` remains the current formal
exact-object contract facade in this predecessor, but the component-map target
does not retain its proactive-delivery exports. They must be removed in an
explicit API-convergence slice, not recreated as reverse aliases. The old
`imagent.proactive_delivery` implementation path is removed.

After that facade convergence, #216 must move only the typed proactive
vocabulary and `validate_delivery_intent` into this seam. It must delete the
historical definitions rather than add reverse compatibility aliases, and it
must not move fingerprint/ID helpers, `_canonical_metadata`, submission/state
records, authorization, JSON ingress, planning, coordination, O2, CLI, or
persistence as part of that contract extraction.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Gateway design](../../design.md)
- [Submissions design](../submissions/design.md)

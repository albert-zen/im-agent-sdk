# Gateway proactive delivery design

## Purpose and boundary

`gateway.delivery.proactive-delivery` accepts a typed `DeliveryIntent`,
authenticates its opaque credential, resolves permitted destinations once,
reserves immutable submission snapshots, and sends each destination through
the same planner, bounded coordinator, and optional O2 observer used by other
Gateway output. It owns orchestration and typed results, not native Channel
encoding, product authentication policy, Agent execution, or a second
delivery runtime.

Authorization completes before route resolution. Full intent/capability
preflight completes before any Channel side effect. The first atomic
reservation pins the destination set and payload/target fingerprints; replay
never follows a moved route. External and Gateway-internal identities are
namespaced by fixed origin plus trusted principal.

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
as a compatibility module. `imagent.contracts` remains the formal
exact-object contract facade while its broader state-contract split is
pending. The old `imagent.proactive_delivery` implementation path is removed.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Gateway design](../../design.md)
- [Submissions design](../submissions/design.md)

# Gateway delivery submissions design

## Purpose and boundary

`gateway.delivery.submissions` owns the stable identity and durable outcome
boundary for one proactive delivery. It reserves the origin/principal-scoped
submission identity and immutable destination snapshots, then permits only
typed per-destination state transitions. It persists identity, snapshots,
receipts, bounded errors, and timestamps; it never persists message or
artifact content and is not a durable job queue or retry scheduler.

The leaf consumes typed delivery contracts, repository transactions, and
resolved route snapshots. Channel side effects remain in delivery
coordination and Channel adapters. Target authorization and route resolution
remain in the separate proactive-authorization and proactive-delivery leaves.

## Identity and state

- a submission ID derives from the SDK-fixed origin, trusted principal ID,
  and caller delivery ID;
- target and payload fingerprints prevent reuse of the same identity for
  different intent;
- destination delivery IDs derive from the submission and stable Conversation
  identity;
- the first atomic reservation pins the complete destination snapshot set;
- reservation identity compares the root fields plus an order-independent map
  of every destination delivery ID to its complete route snapshot; mutable
  outcome, receipt, error, and timestamp fields are not identity;
- a repeated reservation with the same root and destination snapshot set
  returns the existing record; a changed destination ID, Conversation, Thread,
  route, route update time, or reply context fails explicitly;
- destination updates use expected-state comparison and preserve the pinned
  snapshot.

`in_flight` blocks concurrent duplication. `accepted`, `rejected`, `partial`,
and `unknown` are terminal evidence for that attempt. Only an explicit
`retryable` receipt authorizes a later attempt; an ambiguous native outcome is
never silently resent.

## Persistence and recovery

The SQLite implementation shares the single `SQLiteGatewayState` connection
and lock so reservation and destination comparison-and-swap remain atomic.
Restart reconstructs the same immutable record and typed receipts. It does not
recover content or schedule work: the caller must resubmit the same bounded
intent, whose fingerprints are checked against the durable record.

Proactive orchestration checks for an existing submission before resolving a
current Thread route, so an ordinary retry reuses the stored snapshots instead
of conflicting with route movement. The complete snapshot comparison applies
when two first-reservation contenders race after independently resolving their
destinations, and at each repository boundary; it prevents the losing caller
or a non-conforming repository from silently changing the pinned set.

During component rollout, SQLite table/row encoding remains co-located in the
submission module and is therefore an explicit shared path with
`gateway.persistence.sqlite` and `gateway.persistence.row-mapping`. A later
focused persistence split may move that mechanical encoding; it must not
duplicate the repository or change submission transitions.

## Public surface

`imagent.gateway.delivery` is the finite facade for
`DeliverySubmissionOrigin` and the four stable identity/fingerprint helpers.
The formal `imagent.contracts` exports remain exact object aliases while the
broader delivery state-contract split is pending; there is still one
implementation of every value.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [ADR 0010](../../../../decisions/0010-capability-driven-delivery-coordination.md)
- [Gateway design](../../design.md)
- [Persistence design](../../../persistence/design.md)

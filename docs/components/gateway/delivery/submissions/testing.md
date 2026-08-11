# Gateway delivery submissions testing

Focused tests prove that the Gateway delivery facade exposes the exact
submission-owner objects, that the four identity helpers and private content
identity have one implementation owner, and that the historical top-level
implementation path is absent. Clean-process imports prove that the four
helpers are absent from `imagent.contracts` while `DeliverySubmissionOrigin`
is exported only from the Gateway delivery facade, without creating a Gateway
cycle. The proactive
vocabulary is imported from its Gateway owner rather than redefined or
aliased in this leaf.

The proactive delivery suite remains the behavioral authority for atomic
reservation, identity conflicts, pinned destinations, concurrent duplicates,
sticky unknown outcomes, explicit retryable recovery, and typed receipt
round-trips across SQLite restart. Storage tests cover schema migration and
the shared transaction owner. Those tests remain outside this leaf directory
until their later test-convergence slice because moving their surrounding
orchestration fixtures here would mix proactive-delivery behavior into a
mechanical ownership move.

Current-schema row tests also prove that malformed delivery snapshot scope,
timestamp, state, enum, or receipt JSON is rejected after complete typed
record validation. SQLite must leave that evidence untouched and must not
interpret it as permission to retry or resend.

Reservation parity tests must prove that memory and SQLite both accept the
same root plus the same order-independent destination snapshot set, while
rejecting a changed destination ID or any changed snapshot field. The existing
stored outcome may already be terminal and is deliberately excluded from
reservation identity. A SQLite close/reopen must preserve the same comparison.
The proactive suite must separately prove that a normal retry after route
movement uses the stored snapshot, while a first-reservation race whose
contenders resolved different routes fails instead of adopting the winner's
unrelated destination.

Memory capacity tests distinguish `DeliverySubmissionCapacityError` from an
identity conflict, retain stored records at the boundary, and prove concurrent
admission is lock-atomic. Proactive/internal delivery tests prove capacity is a
known pre-side-effect failure, including outer-claim release and the bounded
JSON ingress response. SQLite parity tests must remain unchanged.

Submission-state tests also accept exactly 64 destinations and reject 65
before reservation or SQLite writes, preserving all earlier immutable
destination identity evidence.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_submissions tests.gateway.delivery.test_proactive_delivery -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`, including component-map and
AgentKit validation.

The public reference consumer additionally inspects exact SQLite rows and
database/WAL/sidecar bytes while replaying pinned accepted/unknown records; no
content, artifact path/bytes, credential, or job body may appear.

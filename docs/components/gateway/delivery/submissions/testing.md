# Gateway delivery submissions testing

Focused tests prove that the Gateway delivery facade exposes the exact
submission-owner objects, the historical top-level implementation path is
absent, and clean-process imports do not create a Gateway cycle.

The proactive delivery suite remains the behavioral authority for atomic
reservation, identity conflicts, pinned destinations, concurrent duplicates,
sticky unknown outcomes, explicit retryable recovery, and typed receipt
round-trips across SQLite restart. Storage tests cover schema migration and
the shared transaction owner. Those tests remain outside this leaf directory
until their later test-convergence slice because moving their surrounding
orchestration fixtures here would mix proactive-delivery behavior into a
mechanical ownership move.

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

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_submissions tests.gateway.delivery.test_proactive_delivery -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`, including component-map and
AgentKit validation.

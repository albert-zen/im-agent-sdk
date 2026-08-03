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

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_submissions tests.test_proactive_delivery -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`, including component-map and
AgentKit validation.

# Gateway SQLite persistence testing

SQLite tests must prove:

- each repository operation matches its typed Port and shares the common
  conflict/acquisition types with memory implementations;
- stale binding generations, route identities, checkpoints, correlation states,
  delivery reservations, and destination states fail without partial writes;
- a transaction rollback leaves every previously committed row unchanged;
- completed and protected idempotency evidence survives restart, stale
  `in_flight` reclaim changes the owner atomically, and old owners stay fenced;
- Turn reply correlations remain create-only and request correlations update
  every destination under one request identity only when their current states
  match the caller-supplied expected-state set;
- delivery snapshots, typed receipts, partial/retryable/unknown outcomes, and
  immutable reservation identity survive restart without content;
- supported legacy schemas migrate additively without losing bindings,
  routes, claims, or correlations, and ambiguous legacy reply context is
  cleared rather than guessed;
- current supported rows reconstruct their typed identities; complete
  malformed/cross-Application row rejection remains the separately recorded
  row-mapping validation target rather than a guarantee of the mechanical
  move;
- close serializes with repository access and no second connection or
  transaction owner appears; and
- schema inspection confirms there are no transcript, prompt/response body,
  message/artifact content, credential, callback, spool, outbox, or job-body
  columns.

Focused durable-owner coverage lives in
`tests/gateway/persistence/test_sqlite.py` and the delivery identity suite:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_sqlite \
  tests.gateway.persistence.test_submission_identity -v
```

Every schema change additionally requires a focused legacy-database fixture
and restart test before implementation is accepted.

The supported malformed-row, nullable-scope, migration, restart, rollback,
CAS, claim-fencing, request-transition, and immutable-submission cases are
covered by the owner and row-mapping suites. No SQLite helper or second
transaction owner is retained outside `sqlite.py`.

Current Gateway callers cover only their accepted forward request-state paths.
Direct SQLite repository conformance also rejects a target such as `resolved`
to `open` even when that source state is supplied in `expected_states`, and
proves rollback leaves the request-wide state unchanged.

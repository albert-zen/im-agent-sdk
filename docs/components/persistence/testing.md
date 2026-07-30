# Persistence testing

Required scenarios:

- binding revisions increase monotonically;
- stale put/delete revisions fail;
- managed/flat/fixed binding invariants remain valid;
- completed idempotency survives SQLite restart;
- incomplete/released claims remain retryable;
- projection routes survive restart without transcript content;
- route policy replacement remains deterministic;
- storage rows cannot introduce cross-Application references.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_bindings \
  tests.test_storage \
  tests.test_projection_routing -v
```

Any durable schema change must include an upgrade/compatibility decision before
implementation.

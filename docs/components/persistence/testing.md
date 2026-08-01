# Persistence testing

Required scenarios:

- binding revisions increase monotonically;
- stale put/delete revisions fail;
- managed/flat/fixed binding invariants remain valid;
- completed idempotency survives SQLite restart;
- claims distinguish acquired, completed, and in-flight work;
- stale in-flight leases are reclaimable after restart;
- side-effect-started claims remain sticky across elapsed time and restart
  until an explicit safe release;
- stale outbound leases recover both before submission reservation and after
  an accepted submission whose outer completion write was interrupted;
- overlapping stale and replacement owners are fenced so the stale worker
  cannot mark, complete, or release the replacement's claim;
- projection routes survive restart without transcript content;
- route refresh preserves checkpoints and rejects conflicting explicit values;
- concurrent checkpoint advances use compare-and-swap and cannot overwrite a
  newer boundary;
- a legacy SQLite database without checkpoint/correlation columns migrates
  without losing binding, route, or idempotency rows;
- Turn reply correlation supports exact deletion plus explicit bounded cleanup;
- request correlation persists per successful destination, transitions every
  destination atomically by request, survives restart, and rejects
  zero-selector cleanup;
- a destination inserted after a request-wide response/resolution inherits
  that state instead of reintroducing `open`;
- a later put cannot revive a stale epoch-scoped request correlation;
- opening the previous SQLite schema adds request correlation storage without
  losing existing bridge state;
- proactive route snapshots and per-item receipts survive restart without
  storing message/artifact content;
- concurrent proactive reservation has one winner and mismatched reuse of a
  delivery ID fails;
- SDK-controlled origin and principal namespaces prevent external identities
  from colliding with Gateway-internal projection submissions;
- rejected, in-flight, partial, and unknown proactive states remain sticky
  across restart;
- route policy replacement remains deterministic;
- storage rows cannot introduce cross-Application references.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_bindings \
  tests.test_storage \
  tests.test_projection_hardening \
  tests.test_projection_routing -v
```

Any durable schema change must include an upgrade/compatibility decision before
implementation.

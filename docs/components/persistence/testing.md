# Persistence testing

Required scenarios:

- binding revisions increase monotonically;
- stale put/delete revisions fail;
- memory and SQLite use the exact same repository-contract conflict type, and
  stale failures leave the current binding unchanged;
- managed/flat/fixed binding invariants remain valid;
- completed idempotency survives SQLite restart;
- claims distinguish acquired, completed, and in-flight work;
- stale in-flight leases are reclaimable after restart;
- side-effect-started claims remain sticky across elapsed time and restart
  until an explicit safe release;
- stale outbound leases recover both before submission reservation and after
  an accepted submission whose outer completion write was interrupted;
- overlapping stale and replacement owners are fenced so the stale worker
  cannot refresh, mark, complete, or release the replacement's claim;
- an owner-checked refresh extends only an `in_flight` lease and is required
  before a prepared inbound message enters Gateway processing;
- projection routes survive restart without transcript content;
- the process-local projection repository has one memory owner, starts empty,
  and is not retained by the projection semantic module;
- route refresh preserves checkpoints and rejects conflicting explicit values;
- concurrent checkpoint advances use compare-and-swap and cannot overwrite a
  newer boundary;
- durable destination suppression completes outbound idempotency before
  checkpoint advance and converges the same boundary after SQLite restart
  without storing policy/content or reinvoking the suppressor;
- configured inbound failure presentation completes a fenced pre-acceptance
  claim before error delivery, while the no-presenter path and original
  pre-dispatch cancellation still release it; cancellation after the dispatch
  fence and unknown remain side-effect-started, while post-acceptance remains
  terminal across presenter/Channel failure and restart, without persisting
  exception or rendered content;
- a legacy SQLite database without checkpoint/correlation columns migrates
  without losing binding, route, or idempotency rows;
- Turn reply correlation is create-only/idempotent-same, rejects a different
  destination for the same Thread/Turn in memory and SQLite, survives restart
  unchanged, and supports exact deletion plus explicit bounded cleanup;
- request correlation persists per successful destination, transitions every
  destination atomically by request, survives restart, and rejects
  zero-selector cleanup;
- a destination inserted after a request-wide response/resolution inherits
  that state instead of reintroducing `open`;
- a later put cannot revive a stale epoch-scoped request correlation;
- memory and SQLite independently reject every backward request-correlation
  transition even when the caller names the current state as expected;
- opening the previous SQLite schema adds request correlation storage without
  losing existing bridge state;
- proactive route snapshots and per-item receipts survive restart without
  storing message/artifact content;
- O2 observes only newly executed Coordinator attempts; restart/replay creates
  no notification row, callback state, content copy, cleanup job, or outbox;
- concurrent proactive reservation has one winner and mismatched reuse of a
  delivery ID fails;
- memory and SQLite reservation compare the complete destination-ID-to-route-
  snapshot map independent of tuple order, reject any changed snapshot after
  SQLite restart, and do not mistake mutable terminal outcome fields for a new
  identity;
- process-local submission capacity validates positive configuration, admits
  exactly the configured number of distinct roots under concurrency, preserves
  existing replay/CAS at the bound, and never evicts terminal or ambiguous
  evidence; a new process starts empty and SQLite remains unchanged;
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

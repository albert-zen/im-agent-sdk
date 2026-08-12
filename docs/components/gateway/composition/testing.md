# Gateway composition testing

## Current evidence

- `tests/gateway/test_package_root.py` checks formal facade identity and the
  current owner module, including the absence of the removed historical
  internal module.
- `tests/gateway/test_operations_integration.py` checks frozen groups, defaults, finite
  limit validation, injected repositories, and removed flat constructor
  arguments.
- `tests/gateway/test_vertical_slice.py` proves the composed graph preserves
  the end-to-end no-extension path.

## Required invariants

- canonical `Gateway` acquires one coherent store session, constructs the
  private fenced executor once, and exposes only scoped actions to consumers;
- canonical `Gateway` exposes proactive authorization/delivery through that
  same session and Coordinator, while public request responses use the same
  scoped action executor and projection correlation owner;
- successful scoped bind/observe/workflow routes activate the one projection
  worker immediately, before a later inbound message, without duplicate
  Application subscriptions;
- primitive create-then-bind and create-and-bind both accept the first input
  for an App Server Thread whose native history is not materialized yet,
  including when its turn-bearing continuation read is unavailable while the
  ordinary Thread scope read succeeds;
  preserving the sole bootstrap barrier and one worker in Memory and SQLite;
- a Turn event racing the exact empty baseline remains behind the bootstrap
  barrier and is delivered once, while pre-input Gateway stop followed by
  foreign output restores strict authoritative recovery;
- terminal success replay remains exact under later binding and worker-capacity
  drift, and a new direct/workflow route has a bootstrap barrier before commit;
- a one-slot create-and-bind workflow transfers capacity from the previous
  foreground Thread without a live-before-baseline window, and caller
  cancellation after commit still joins D-owned route reconciliation and exact
  bootstrap-lease release;
- async-context shutdown joins lease renewal, Controller, projection workers,
  Channels, Applications, session, and store resources;
- post-acquisition construction failure closes the lease/store, lease-renewal
  loss cancels even blocked startup and closes live admission/adapters, and
  retained action surfaces reject work after shutdown;
- each public composition symbol has one exact implementation identity;
- the private runtime dependency bundle, limits, and extensions are immutable and typed;
- missing optional dependencies preserve documented defaults and missing
  extensions preserve behavior exactly;
- Controller composition rejects loose or mixed repositories before input,
  while one exact coherent store session supplies every runtime persistence
  owner and C's effect executor, and the projection-owned route reconciliation
  callable converges successful/replayed action state, without escaping through
  `ConversationActions`;
- that injected action-route seam cannot report success after projection stop
  begins or after its worker exits during baseline, and terminal replay after
  lifecycle restart converges the existing durable result;
- the injected commit-fence seam gives stop and a new route transaction one
  winner without exposing the session: preflight-stop is no-write failed,
  entered-commit-stop is post-durable partial, terminal replay is unchanged,
  and real Memory/SQLite Controller graphs apply the same rule to a known
  foreground workflow binding/route commit;
- invalid finite capacities, including the default in-memory idempotency record
  bound, plus invalid finite timeout/retention/retry values fail during
  construction before startup or I/O;
- invalid Application summary/capability contracts, malformed Channel/store
  structure, and constructor-to-start identity drift fail before store
  acquisition for Memory and SQLite compositions; an acquired malformed or
  mismatched session is closed before runtime construction;
- the positive active-Thread observation bound reaches the one projection
  runtime, rejects only a distinct Thread before Application subscription or
  projection delivery work, and has no durable slot state; a full
  `foreground_only` switch leaves the prior Conversation binding and candidate
  route unchanged, then succeeds after the occupied worker releases its slot;
- the default in-memory idempotency repository receives the configured bound,
  rejects a new identity explicitly before media/planning/Channel work, and
  leaves an injected repository unwrapped and unconfigured;
- the graph contains one Channel admission path and one Application observer
  per Thread, with no locator, generic hook, transcript, spool, or runtime;
- the historical `imagent.gateway_composition` internal module cannot be
  imported.

`tests/gateway/test_reference_consumer.py` is the canonical public composition
acceptance. It uses the same entry point as the installed-wheel smoke and
rejects private store/session/executor/repository imports from the example. Its
Block G path covers two-recipient request first-writer/restart semantics,
no-snapshot staleness, media/trust bounds before native send, consumer-owned
artifact cleanup, pinned proactive routes, isolated unknown, and byte-level
SQLite/WAL/sidecar non-persistence.

The target focused suite is `tests/gateway/test_composition.py`, with facade
checks retained in `tests/gateway/test_package_root.py`. Until focused group
coverage moves, run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_package_root tests.gateway.test_operations_integration tests.gateway.test_vertical_slice -v
```

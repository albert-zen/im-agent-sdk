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

- each public composition symbol has one exact implementation identity;
- repositories, limits, and extensions are immutable and typed;
- missing optional dependencies preserve documented defaults and missing
  extensions preserve behavior exactly;
- Controller composition rejects loose or mixed repositories before input,
  while one exact coherent store session supplies every runtime persistence
  owner and C's effect executor, and the projection-owned route reconciliation
  callable converges successful/replayed action state, without escaping through
  `ConversationActions`;
- invalid finite capacities, including the default in-memory idempotency record
  bound, fail during construction before startup or I/O;
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

The target focused suite is `tests/gateway/test_composition.py`, with facade
checks retained in `tests/gateway/test_package_root.py`. Until focused group
coverage moves, run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_package_root tests.gateway.test_operations_integration tests.gateway.test_vertical_slice -v
```

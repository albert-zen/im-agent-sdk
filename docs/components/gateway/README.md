# Gateway components

Gateway composes the single bridge path: admission, Conversation routing,
Application dispatch and observation, projection/recovery, bounded delivery,
bridge persistence, and read-only diagnostics. It owns no product command,
Channel transport, Agent transcript, or second runtime.

## Current leaf navigation

- [composition design](composition/design.md) and
  [testing](composition/testing.md) — immutable repository, limit, and typed
  extension groups plus explicit graph wiring.
- [lifecycle design](lifecycle/design.md) and
  [testing](lifecycle/testing.md) — bounded startup admission, rollback, and
  shutdown ordering, with one mandatory two-argument Channel start path.
- [admission design](admission/design.md) and
  [testing](admission/testing.md) — fenced durable inbound identity acquired
  before Channel media work, without a legacy signature fallback.
- [concurrency design](concurrency/design.md) and
  [testing](concurrency/testing.md) — dependency-neutral waiter-safe keyed
  serialization and optional active-key capacity mechanics.
- [input](input/README.md) — independent content-transformation, dispatch, and
  failure-presentation leaves.
- [routing](routing/README.md) — Conversation bindings, typed Gateway
  operations, and outbound projection-route policy kept as separate
  authorities.
- [projection](projection/README.md) — one Thread-scoped Application observer,
  per-route completion checkpoints, minimal request/reply correlation, and
  bounded authoritative recovery.
- [delivery](delivery/README.md) — deterministic planning and the remaining
  coordination/submission/proactive/outcome leaves as they move in focused
  slices.
- [presentation design](presentation/design.md) and
  [testing](presentation/testing.md) — O1 bounded per-destination projection
  presentation and suppression.
- [diagnostics design](diagnostics/design.md) and
  [testing](diagnostics/testing.md) — Gateway-owned I1/I2/O1/O2,
  projection/startup aggregation, bounded provider normalization, and the
  exact stable transition facade.
- [persistence](persistence/README.md) — bridge-state contracts and repository
  implementations, including process-local [memory](persistence/memory/design.md)
  and its [tests](persistence/memory/testing.md).
- [Gateway aggregate design](design.md) and [testing](testing.md) — current
  orchestration evidence while the remaining leaves are extracted.

The machine-readable [component map](../component-map.yml) is authoritative for
the complete target leaf inventory, current/target paths, dependencies, tests,
and explicit gaps.

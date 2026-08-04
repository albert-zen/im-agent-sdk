# Gateway persistence components

Gateway persistence contains the passive bridge-state contracts, repository
ports, process-local references, fenced idempotency, and the intentional
single-transaction SQLite implementation. It stores no transcript, content,
credential, Agent execution state, durable job, spool, or outbox.

## Leaves

- [state contracts](state-contracts/design.md) and
  [testing](state-contracts/testing.md) — immutable binding, route,
  checkpoint, correlation, and submission records;
- [repository contracts](repository-contracts/design.md) and
  [testing](repository-contracts/testing.md) — atomic typed mutation ports and
  conflicts;
- [memory](memory/design.md) and [testing](memory/testing.md) — process-local
  reference repositories, rebuilt empty after restart;
- [idempotency](idempotency/design.md) and
  [testing](idempotency/testing.md) — fenced inbound/outbound claims;
- [SQLite](sqlite/design.md) and [testing](sqlite/testing.md) — the shared
  durable transaction owner;
- [row mapping](row-mapping/design.md) and
  [testing](row-mapping/testing.md) — pure SQLite record conversion.

The [component map](../../component-map.yml) records current/target code and
remaining focused extraction gaps.

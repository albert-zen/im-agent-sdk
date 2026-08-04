# Gateway persistence components

Gateway persistence contains the passive bridge-state contracts, repository
ports, process-local references, fenced idempotency, and the intentional
single-transaction SQLite implementation. It stores no transcript, content,
credential, Agent execution state, durable job, spool, or outbox.

## Leaves

- state contracts — immutable binding, route, checkpoint, correlation, and
  submission records;
- repository contracts — atomic typed mutation ports and conflicts;
- [memory](memory/design.md) and [testing](memory/testing.md) — process-local
  reference repositories, rebuilt empty after restart;
- idempotency — fenced inbound/outbound claims;
- SQLite — the shared durable transaction owner;
- row mapping — pure SQLite record conversion.

The [component map](../../component-map.yml) records current/target code and
remaining focused extraction gaps.

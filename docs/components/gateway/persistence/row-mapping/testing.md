# Gateway SQLite row mapping testing

Row-mapping tests must prove:

- each supported row round-trips the exact stable typed references, enum,
  timestamps, optional values, checkpoint pair, response shape, route
  snapshot, receipt, and correlation state;
- target validation distinguishes `NULL` from empty identifiers and rejects
  malformed timestamps, JSON, enums, references, cross-Application scopes,
  checkpoint pairs, receipts, and non-null delivery error/detail text
  explicitly;
- every decoded Thread-bearing row requires a non-empty same-Application
  Project ID; the historical empty Project sentinel is rejected;
- decoding performs no SQL mutation, migration, clock read, file/native I/O,
  policy selection, retry, or recovery side effect;
- the target pure mapper excludes `merge_projection_route`; endpoint-conflict
  and checkpoint-preservation behavior stays covered by the SQLite mutation
  owner tests;
- deterministic SQL observation order does not become delivery-reservation
  identity order; and
- legacy shapes are accepted only after the SQLite migration owner has
  transformed them into the current explicit schema.

Focused pure-mapping coverage lives in
`tests/gateway/persistence/test_row_mapping.py` and runs without opening a
database. Durable integration coverage remains in
`tests/gateway/persistence/test_sqlite.py`:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_row_mapping \
  tests.gateway.persistence.test_sqlite -v
```

The focused suite proves the exact mapper output and identities, bounded
response/receipt JSON validation, NULL/empty-string sentinel and enum behavior,
timezone failure, deterministic malformed-row errors, and side-effect freedom.
The durable suite injects malformed current rows and proves that reads fail
without mutating, repairing, replaying, or widening authority. It also opens
the immediately supported legacy schema, proves that the SQLite owner performs
its documented upgrade, and verifies that the resulting valid rows survive
restart. The legacy fixture is never handed directly to a mapper.

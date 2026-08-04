# Gateway SQLite row mapping testing

Row-mapping tests must prove:

- each supported row round-trips the exact stable typed references, enum,
  timestamps, optional values, checkpoint pair, response shape, route
  snapshot, receipt, and correlation state;
- target validation distinguishes `NULL` from empty identifiers and rejects
  malformed timestamps, JSON, enums, references, cross-Application scopes,
  checkpoint pairs, and receipts explicitly;
- decoding performs no SQL mutation, migration, clock read, file/native I/O,
  policy selection, retry, or recovery side effect;
- the target pure mapper excludes `merge_projection_route`; endpoint-conflict
  and checkpoint-preservation behavior stays covered by the SQLite mutation
  owner tests;
- deterministic SQL observation order does not become delivery-reservation
  identity order; and
- legacy shapes are accepted only after the SQLite migration owner has
  transformed them into the current explicit schema.

Current coverage runs through the durable adapter:

```sh
PYTHONPATH=src python -m unittest tests.test_storage -v
```

Focused durable-adapter coverage injects malformed current rows and proves
that reads fail without mutating, repairing, replaying, or widening authority.
It also opens the immediately supported legacy schema, proves that the SQLite
owner performs its documented upgrade, and verifies that the resulting valid
rows survive restart. The legacy fixture is never handed directly to a mapper.

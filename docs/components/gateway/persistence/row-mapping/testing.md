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

Current adapter coverage does not prove all target validation: nullable
binding scope can be silently collapsed and some decoded values bypass the
complete validator. The mechanical extraction adds isolated round-trip tests
in `tests/gateway/persistence/test_row_mapping.py` while retaining that known
behavior. A separate behavior/migration slice must add malformed-row tests and
close the validation gap before the target rejection claims become enforced.

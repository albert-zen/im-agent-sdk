# Gateway repository contracts testing

Repository-contract tests and implementation conformance must prove:

- memory and SQLite raise the exact `BindingConflict` type for stale expected
  revisions;
- a conflict does not mutate or delete the current binding;
- repository Protocol signatures remain structurally compatible with every
  implementation and fake;
- conflict and acquisition outcomes contain no message content, credentials,
  callback, retry work, or native Application state;
- expected revisions, owner tokens, stable identities, and checkpoint values
  remain explicit rather than inferred from text or timestamps.

For the binding extraction run:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_memory \
  tests.test_storage \
  tests.test_adapter_contracts -v
```

The complete repository-Port extraction will add
`tests/gateway/persistence/test_repository_contracts.py`. Every focused slice
also runs the full repository gates.

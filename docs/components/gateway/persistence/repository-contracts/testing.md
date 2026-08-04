# Gateway repository contracts testing

Repository-contract tests and implementation conformance must prove:

- the owner module is importable in a clean process and every moved name has
  one implementation owner;
- `imagent.adapters` and `imagent.gateway.persistence` expose the exact same
  objects as `gateway.persistence.repository_contracts`;
- moved Protocol method signatures, annotations, defaults, and runtime
  `typing.get_type_hints` remain exact;
- enum values and every conflict/capacity exception preserve identity and
  behavior;
- memory and SQLite raise the exact `BindingConflict` type for stale expected
  revisions;
- a conflict does not mutate or delete the current binding;
- repository Protocol signatures remain structurally compatible with every
  implementation and fake;
- conflict and acquisition outcomes contain no message content, credentials,
  callback, retry work, or native Application state;
- expected revisions, owner tokens, stable identities, and checkpoint values
  remain explicit rather than inferred from text or timestamps.
- memory and SQLite accept every forward or same-state request-correlation
  edge, reject every backward edge even when the current state appears in
  `expected_states`, and leave every destination unchanged on conflict;
- request-wide transition compare-and-swap and late-destination inheritance
  remain atomic across the same epoch-scoped request identity.

For the repository-Port extraction run:

```sh
PYTHONPATH=src:tests python -m unittest \
  tests.gateway.persistence.test_repository_contracts \
  tests.gateway.persistence.test_memory \
  tests.gateway.persistence.test_sqlite \
  tests.conformance.test_adapter_contracts -v
```

Every focused slice also runs the full repository gates and the clean-wheel
smoke check.

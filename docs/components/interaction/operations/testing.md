# Interaction operations testing

## Contract scenarios

Tests for `interaction.operations` must prove:

- `operationId` and other identifiers reject empty or more-than-512-character
  values;
- success and failure status values remain stable and schema-aligned;
- owner-specific operations and results reject mismatched identity or
  discriminants using the common validation vocabulary;
- every public error code is stable, explicit, and maps representative native
  exceptions without implying hidden fallback or retry;
- Gateway-owned `MissingBindingError` and `StaleBindingError` preserve exact
  `missing_binding`/`stale_binding` projections without making Interaction
  import Gateway binding truth;
- `capacity_exhausted` is retryable only for a proved pre-side-effect capacity
  rejection;
- unsupported behavior remains unsupported rather than being silently
  approximated;
- free-form Metadata is not accepted in place of typed common arguments or
  success fields;
- common helpers remain stateless and do not create a claim, operation log,
  service locator, or generic mutable context.
- repository runtime code imports the owning
  `imagent.interaction.operations` leaf directly, while the historical
  cross-layer `imagent.contracts` module is absent;
- Applications-owned request exceptions preserve their existing stable error
  codes through a one-way dependency and cannot make Interaction import an
  Application or request implementation.

Focused evidence is:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.test_operations \
  tests.interaction.test_contracts -v
python scripts/validate_schemas.py
```

Common cases live in `tests/interaction/test_operations.py`. Application and
Gateway variant tests move to their owning test trees rather than being copied
into the Interaction suite. The public facade remains covered as a
compatibility identity, not a second implementation.

Any schema or public-contract change also requires operation/result union
parity, affected adapter and Controller conformance, component-map validation,
and the repository-wide checks in `AGENTS.md`.

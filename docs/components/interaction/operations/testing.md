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
- unsupported behavior remains unsupported rather than being silently
  approximated;
- free-form Metadata is not accepted in place of typed common arguments or
  success fields;
- common helpers remain stateless and do not create a claim, operation log,
  service locator, or generic mutable context.

Current focused evidence is:

```sh
PYTHONPATH=src python -m unittest tests.test_contracts -v
python scripts/validate_schemas.py
```

During mechanical separation, the common cases move once to
`tests/interaction/test_operations.py`. Application and Gateway variant tests
move to their owning test trees rather than being copied into the Interaction
suite.

Any schema or public-contract change also requires operation/result union
parity, affected adapter and Controller conformance, component-map validation,
and the repository-wide checks in `AGENTS.md`.

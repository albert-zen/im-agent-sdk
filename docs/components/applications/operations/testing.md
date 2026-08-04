# Application operations testing

The current executable owner mirror is
`tests/applications/test_operations.py`. It is the focused suite for this
leaf; adapter conformance and input integration suites remain separate,
affected evidence rather than alternate owners.

Tests cover closed operation/result discriminants, stable operation IDs,
reference scoping, explicit unsupported/error results, archive versus permanent
deletion honesty, and exact result matching. They prove native activation is
separate from binding, a concrete native Thread-create option mapping cannot
widen `CreateThread`, and a consumer-only command never enters the common
union without shared evidence.

The focused owner suite proves that the Applications module is the sole
implementation and public surface for Application operation/result values and
validators. Clean subprocesses prove that the package root and
`imagent.contracts` do not retain the retired Application names. Request
response shapes remain the canonical values from `applications.requests`;
Gateway operation/result values and validators remain outside this leaf.

Input-mutating operations must preserve the pre-dispatch/unknown-outcome
boundary and must not auto-retry after an ambiguous native mutation.

```sh
uv run python -m unittest tests.applications.test_operations -v

# Affected integration evidence (not this leaf's implementation owner)
PYTHONPATH=src uv run python -m unittest \
  tests.conformance.test_adapter_contracts tests.applications.adapters.appserver.test_input_integration -v
uv run python scripts/validate_schemas.py
```

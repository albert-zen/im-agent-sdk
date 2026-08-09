# Application operations testing

The current executable owner mirror is
`tests/applications/test_operations.py`. It is the focused suite for this
leaf; adapter conformance and input integration suites remain separate,
affected evidence rather than alternate owners.

Tests cover closed operation/result discriminants, stable operation IDs,
required Project scoping for Thread list/create and every Thread-targeted
operation, bounded managed Project CWD creation, exact managed Project deletion,
bounded Thread title/context payloads, bounded list queries/cursors/pages,
history/catch-up adapters that exceed the requested limit, schema parity for
opaque attachment handles, strict non-Boolean attachment sizes, typed text
content/format validation, legacy generic Project-delete rejection, explicit
unsupported/error results,
archive versus permanent
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
The deterministic managed fake must converge repeated Project creation by the
same stable operation ID and reject deletion of a non-empty Project;
fixed/flat fakes and native adapters return typed unsupported without mutating
their one workspace Project. Durable native mutation receipts and workflow
outcome algebra remain block B.

```sh
uv run python -m unittest tests.applications.test_operations -v

# Affected integration evidence (not this leaf's implementation owner)
PYTHONPATH=src uv run python -m unittest \
  tests.conformance.test_adapter_contracts tests.applications.adapters.appserver.test_input_integration -v
uv run python scripts/validate_schemas.py
```

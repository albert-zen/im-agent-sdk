# Application operations testing

Current test evidence is `tests/test_contracts.py`,
`tests/test_adapter_contracts.py`, and `tests/test_appserver_input.py`; the
target focused suite is `tests/applications/test_operations.py`.

Tests cover closed operation/result discriminants, stable operation IDs,
reference scoping, explicit unsupported/error results, archive versus permanent
deletion honesty, and exact result matching. They prove native activation is
separate from binding, a concrete native Thread-create option mapping cannot
widen `CreateThread`, and a consumer-only command never enters the common
union without shared evidence.

Input-mutating operations must preserve the pre-dispatch/unknown-outcome
boundary and must not auto-retry after an ambiguous native mutation.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.test_contracts tests.test_adapter_contracts tests.test_appserver_input -v
uv run python scripts/validate_schemas.py
```

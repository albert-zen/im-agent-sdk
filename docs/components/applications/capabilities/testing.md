# Application capabilities testing

Current coverage lives in `tests/test_contracts.py` and
`tests/test_adapter_contracts.py`; target focused coverage is
`tests/applications/test_capabilities.py`.

Tests validate every enum/discriminant, project mode and Thread deletion
declaration, attachment-source support, replay/order/request/runtime claims,
and rejection of inconsistent capability combinations. Adapter conformance
must prove a capability maps to real native behavior or an explicit
unsupported result—never a hidden fallback or product policy.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.test_contracts tests.test_adapter_contracts -v
uv run python scripts/validate_schemas.py
```

Any capability change also requires concrete adapter evidence from the
applicable integrations and the repository-wide validation gates.

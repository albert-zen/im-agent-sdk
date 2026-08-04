# Application capabilities testing

Focused ownership coverage lives in `tests/applications/test_capabilities.py`;
`tests/conformance/test_adapter_contracts.py` retains the cross-adapter conformance
evidence.

Tests validate every enum/discriminant, project mode and Thread deletion
declaration, attachment-source support, replay/order/request/runtime claims,
rejection of inconsistent capability combinations, and that importing the
capability owner does not initialize concrete adapters while existing lazy
facade exports retain exact object identity. Adapter conformance
must prove a capability maps to real native behavior or an explicit
unsupported result—never a hidden fallback or product policy.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.test_capabilities -v
PYTHONPATH=src uv run python -m unittest tests.conformance.test_adapter_contracts -v
uv run python scripts/validate_schemas.py
```

Any capability change also requires concrete adapter evidence from the
applicable integrations and the repository-wide validation gates.

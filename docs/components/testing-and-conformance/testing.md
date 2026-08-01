# Testing and conformance testing

The contract kit tests itself against fake adapters, then runs against Codex,
Zen, T3, and the available Channel seam.

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_adapter_contracts -v
```

The full suite remains the acceptance check:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/validate_schemas.py
ruff check src tests scripts
ruff format --check src tests scripts
pyright src tests scripts
```

When adding a new adapter:

1. document native source of truth and capability limits;
2. pass the common contract kit without false fallback claims;
3. add focused native mapping/recovery tests;
4. add a second-integration proof before generalizing new semantics.

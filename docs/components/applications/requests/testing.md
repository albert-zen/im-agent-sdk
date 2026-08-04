# Application requests testing

Current coverage is `tests/test_contracts.py` and
`tests/test_appserver_requests.py`; target focused coverage is
`tests/applications/test_requests.py`.

Tests prove Application-scoped request identity, bounded approval choices and
user-input questions, typed response shape validation, explicit unsupported
request kinds, and exact open/respond/resolve discriminants. Native fixture
tests cover response wire fidelity, terminal resolution racing response
writeback, duplicate responses, and epoch-scoped staleness after reset.

Recovery tests must prove an adapter uses an authoritative pending-request set
when it advertises one and otherwise emits a truthful stale projection rather
than inventing an open request. Neither request values nor tests may make a
consumer permission or destination policy part of the Application contract.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.test_contracts tests.test_appserver_requests -v
uv run python scripts/validate_schemas.py
```

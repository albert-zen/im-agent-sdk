# Application requests testing

Current coverage is `tests/test_contracts.py` and the adapter-owned
`tests/applications/adapters/appserver/test_requests.py`; the retained Gateway
integration evidence is `tests/test_appserver_requests.py`. Target focused
coverage is `tests/applications/test_requests.py`.

Tests prove Application-scoped request identity, bounded approval choices and
user-input questions, typed response shape validation, explicit unsupported
request kinds, and exact open/respond/resolve discriminants. Native fixture
tests cover response wire fidelity, terminal resolution racing response
writeback, duplicate responses, and epoch-scoped staleness after reset.

Recovery tests must prove an adapter uses an authoritative pending-request set
when it advertises one and otherwise emits a truthful stale projection rather
than inventing an open request. Neither request values nor tests may make a
consumer permission or destination policy part of the Application contract.

The focused owner suite also proves that `imagent.applications.requests` is the
sole implementation and public owner. Clean-process import-order cases prove
that the package root and `imagent.contracts` do not retain the retired request
names, while runtime annotation resolution continues to point at the canonical
Application resource and request owners. Gateway route-correlation records and
their validator remain outside this leaf.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.applications.test_requests \
  tests.test_contracts tests.applications.adapters.appserver.test_requests \
  tests.test_appserver_requests -v
uv run python scripts/validate_schemas.py
```

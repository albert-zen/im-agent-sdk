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

The focused owner suite also proves that `imagent.applications.requests` is the
sole implementation owner, the `imagent.contracts` facade preserves exact
object identities, and the historical model/operations/validation modules no
longer define a second request contract. Gateway route-correlation records and
their validator remain outside this leaf. Clean-process import-order cases and
runtime annotation resolution guard the temporary resource-identity cycle
while the Application contract finishes its later focused split.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.applications.test_requests \
  tests.test_contracts tests.test_appserver_requests -v
uv run python scripts/validate_schemas.py
```

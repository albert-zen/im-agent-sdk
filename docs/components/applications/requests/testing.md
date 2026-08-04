# Application requests testing

The current executable owner mirror is
`tests/applications/test_requests.py`. It is the focused suite for this leaf;
adapter-native and Gateway request-correlation suites remain separate,
affected evidence rather than alternate owners.

Tests prove Application-scoped request identity, bounded approval choices and
user-input questions, typed response shape validation, explicit unsupported
request kinds, and exact open/respond/resolve discriminants. Native fixture
tests cover response wire fidelity, terminal resolution racing response
writeback, duplicate responses, and epoch-scoped staleness after reset.

Recovery tests must prove an adapter uses an authoritative pending-request set
when it advertises one and otherwise emits a truthful stale projection rather
than inventing an open request. Neither request values nor tests may make a
consumer permission or destination policy part of the Application contract.

The focused owner suite proves that `imagent.applications.requests` is the
sole implementation and public owner. Clean-process import-order cases prove
that the package root and `imagent.contracts` do not retain the retired request
names, while runtime annotation resolution continues to point at the canonical
Application resource and request owners. Gateway route-correlation records and
their validator remain outside this leaf.

```sh
uv run python -m unittest tests.applications.test_requests -v

# Affected integration evidence (not this leaf's implementation owner)
PYTHONPATH=src uv run python -m unittest \
  tests.applications.adapters.appserver.test_requests \
  tests.applications.adapters.appserver.test_gateway_request_integration -v
uv run python scripts/validate_schemas.py
```

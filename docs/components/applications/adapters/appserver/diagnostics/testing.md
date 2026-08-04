# App Server diagnostics testing

## Current evidence

App Server diagnostic facts are covered by
`tests/applications/adapters/appserver/test_client.py`,
`tests/test_appserver_transport.py`, and the Interaction diagnostics contract
tests in `tests/interaction/test_diagnostics.py`; Gateway aggregation evidence
is in `tests/gateway/test_diagnostics.py`. The evidence includes
ready/reconnecting/disconnected epochs, independent notification and
server-request queue depth/overflow, fixed failure codes, and the bounded
ADR-0014 `ConnectionDiagnosticFacts` surface. Epoch and queue overflow/reset
evidence is primarily in the transport suite.

The legacy debug-summary/logging helpers are not proven to have the same
redaction boundary: current summaries may retain native identifiers and
content-derived previews. Focused security tests are still required before
those helpers may be described as bounded or safe.

Application diagnostic identity is covered by
`tests/applications/test_diagnostics.py`; the App Server mutable state remains
owned and tested here, rather than moving into that contract module. The tests
also keep T3's no-synthetic-connection result distinct; that is
affected cross-adapter evidence, not an App Server diagnostic dependency.

## Target evidence and verification

The target focused suite is
`tests/applications/adapters/appserver/test_diagnostics.py`. Run it together
with the retained cross-component evidence:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_diagnostics -v
uv run python -m unittest tests.interaction.test_diagnostics -v
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_client -v
uv run python -m unittest tests.test_appserver_transport -v
```

The physical move keeps diagnostic reads side-effect free and bounded, then
requires every AGENTS gate, component-map/AgentKit check, and clean-wheel
smoke. Legacy debug summaries remain outside the ADR-0014 fact guarantee;
their security hardening is a later slice.

## Authority

- [Diagnostics design](design.md)
- [Interaction diagnostics design](../../../../interaction/diagnostics/design.md)
- [Gateway diagnostics design](../../../../gateway/diagnostics/design.md)
- [App Server testing context](../../../../application-adapters/testing.md)

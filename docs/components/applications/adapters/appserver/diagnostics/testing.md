# App Server diagnostics testing

## Current evidence

App Server diagnostic facts are covered by `tests/test_appserver_client.py`,
`tests/test_appserver_transport.py`, and `tests/test_diagnostics.py`
(`DiagnosticsSurfaceTests`). The evidence includes
ready/reconnecting/disconnected epochs, independent notification and
server-request queue depth/overflow, fixed failure codes, and the bounded
ADR-0014 `ConnectionDiagnosticFacts` surface. Epoch and queue overflow/reset
evidence is primarily in the transport suite.

The legacy debug-summary/logging helpers are not proven to have the same
redaction boundary: current summaries may retain native identifiers and
content-derived previews. Focused security tests are still required before
those helpers may be described as bounded or safe.

The tests also keep T3's no-synthetic-connection result distinct; that is
affected cross-adapter evidence, not an App Server diagnostic dependency.

## Target evidence and verification

The future mirrored path is
`tests/applications/adapters/appserver/test_diagnostics.py`. Run current
evidence with:

```sh
uv run python -m unittest tests.test_diagnostics -v
uv run python -m unittest tests.test_appserver_client -v
uv run python -m unittest tests.test_appserver_transport -v
```

The later physical split must keep diagnostic reads side-effect free and
bounded, then run every AGENTS gate, component-map/AgentKit check, and
clean-wheel smoke.

## Authority

- [Diagnostics design](design.md)
- [Repository diagnostics design](../../../../diagnostics/design.md)
- [App Server testing context](../../../../application-adapters/testing.md)

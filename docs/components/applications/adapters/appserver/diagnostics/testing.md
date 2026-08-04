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

Focused security evidence in
`tests/applications/adapters/appserver/test_diagnostics.py` proves the fixed
`appserver.debug.v1` transport, text, event, and health record schemas. It
checks exact limits and limit-plus-one collection behavior before sampling or
sorting; validates that `max_preview_chars` is positive, non-boolean, and no
greater than 256; and proves text previews are always absent. Nested response,
payload, permission, question, change, event, and health inputs use sensitive
sentinels for IDs, content, paths, commands, questions, endpoint/userinfo,
and credentials; none may survive a returned summary or captured log record.
The scalar-cap-plus-one, path-key, and overlong-key cases prove that no
fingerprint is retained and no SHA helper runs after a capped or sensitive key
or text. Samples otherwise use only deterministic SHA-256 key fingerprints
and fixed structural type/count/length facts. Managed media, single and
embedded Unix paths, Windows drive/UNC paths, and arbitrary-scheme endpoints
or userinfo remain fail-closed redactions. Counter-cap-plus-one evidence fixes
the health epoch/retry bound as well. Counting mapping and sequence fixtures
prove that a structural summary consumes no fifth native item while taking its
four-item sample.

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

The snapshot tests preserve the exact ADR-0014 typed fact values and prove
reads remain side-effect-free; debug hardening may not alter epoch, counter,
queue, state, or failure-code semantics. The physical owner remains unchanged
and requires every AGENTS gate, component-map/AgentKit check, and clean-wheel
smoke.

## Authority

- [Diagnostics design](design.md)
- [Interaction diagnostics design](../../../../interaction/diagnostics/design.md)
- [Gateway diagnostics design](../../../../gateway/diagnostics/design.md)
- [App Server testing context](../../../../application-adapters/testing.md)

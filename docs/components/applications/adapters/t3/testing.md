# T3 Application adapter testing

## Current evidence

`tests/test_t3_client.py` (`HttpT3ClientTests`) currently covers one
authenticated happy-path request/response flow. It does not yet cover
non-object responses, bounded/redacted error values, or the full client
lifecycle; those remain required target cases.
`tests/conformance/test_adapter_contracts.py` exercises
the public Application contract. `tests/test_gateway_vertical_slice.py`
covers T3 Projects/Threads/history/input and distinct native behavior.
`tests/applications/presentation/test_live_activity.py` covers the optional
recoverable activity presenter, stable identity, its finite presentation
window, cancellation, and replay-safe history association.

Evidence must preserve native acceptance before presenter work, stable IDs,
explicit gaps, authoritative history recovery, no synthetic connection
diagnostics, and unsupported interactive requests. Focused adapter tests must
also establish finite eviction/capacity for poll and dedupe state before that
state is described as bounded.

## Target evidence and verification

The target mirrored suite is `tests/applications/adapters/test_t3.py`; the
existing conformance, vertical, and presentation suites remain affected
evidence until physical test movement. Run:

```sh
uv run python -m unittest tests.test_t3_client -v
uv run python -m unittest tests.conformance.test_adapter_contracts -v
uv run python -m unittest tests.test_gateway_vertical_slice -v
```

Later physical reorganization must run full unittest discovery, all AGENTS
gates, AgentKit checks, and clean-wheel smoke.

## Authority

- [T3 design](design.md)
- [T3 transition page](../../../application-adapters/adapters/t3.md)
- [Adapter testing context](../../../application-adapters/testing.md)

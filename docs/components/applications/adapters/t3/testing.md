# T3 Application adapter testing

## Current evidence

`tests/applications/adapters/test_t3.py` (`HttpT3ClientTests` plus focused
adapter cases) covers the authenticated HTTP request/response flow, target
facade identity, and the co-located owner. It also covers baseline admission,
active-lock capacity, deterministic seen-message/terminal-Turn eviction, and
native-history recovery after finite dedupe retention.
`tests/conformance/test_adapter_contracts.py` exercises
the public Application contract. `tests/gateway/test_vertical_slice.py`
covers T3 Projects/Threads/history/input and distinct native behavior.
`tests/applications/presentation/test_live_activity.py` covers the optional
recoverable activity presenter, stable identity, its finite presentation
window, cancellation, and replay-safe history association.

Evidence must preserve native acceptance before observation/presenter work,
stable IDs, explicit gaps, authoritative history recovery, no synthetic
connection diagnostics, and unsupported interactive requests. Focused owner
tests must prove callback failure performs zero dispatch; dispatch, follow-up
read, cancellation, and missing-ID failures become one
`ApplicationInputOutcomeUnknown` with no fallback; the baseline reservation is
released for later capacity use; and `AcceptedTurn` returns before any
post-identity publish/presentation work. They must also prove active baseline
authority is never evicted, capacity rejects before callback/native mutation,
send-lock waiters/owners are cancellation-safe, and cache windows evict the
oldest stable identity deterministically without creating a local transcript
or durable spool.

## Target evidence and verification

The target owner suite is `tests/applications/adapters/test_t3.py`; the
existing conformance, vertical, diagnostics, and presentation suites remain
affected evidence. Run:

```sh
uv run python -m unittest tests.applications.adapters.test_t3 -v
uv run python -m unittest tests.conformance.test_adapter_contracts -v
uv run python -m unittest tests.gateway.test_vertical_slice -v
```

Later physical reorganization must run full unittest discovery, all AGENTS
gates, AgentKit checks, and clean-wheel smoke.

## Authority

- [T3 design](design.md)
- [T3 transition page](../../../application-adapters/adapters/t3.md)
- [Adapter testing context](../../../application-adapters/testing.md)

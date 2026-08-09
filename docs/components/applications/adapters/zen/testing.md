# Zen Application adapter testing

## Current evidence

Zen is covered by the shared App Server suites and vertical evidence:

- `tests/applications/adapters/test_zen.py` and the retained
  `tests/applications/adapters/appserver/test_input_integration.py` prove native start, profile/options,
  connection reset, and no Codex steer policy;
- `tests/applications/adapters/appserver/test_requests.py` proves
  command-approval mapping and explicit unsupported request methods; the
  retained Gateway integration case remains in
  `tests/applications/adapters/appserver/test_gateway_request_integration.py`;
- `tests/applications/presentation/test_artifact_materialization.py` proves
  the optional shared artifact position without adding Codex live activity;
- `tests/gateway/test_vertical_slice.py` proves Zen and Codex remain distinct
  Application kinds and native outcome behavior.

The tests must keep Zen's truthful `started/create_new` result, native history
recovery, request cardinality/bounds, and explicit unsupported capability. A
shared transport fixture is not evidence for a Codex-only Zen feature.

They also prove one stable workspace Project/list/get surface across adapter
reconstruction, canonical-root fingerprint changes when the configured root
changes, mandatory Project ancestry throughout mapping, and explicit
unsupported native Project creation without a client call.
Shared App Server request evidence additionally proves that workspace scope is
verified before request state or native effects and is not re-read after a
successful native response.

## Target evidence and verification

The target owner suite is `tests/applications/adapters/test_zen.py`; the
retained input and vertical suites remain affected cross-component evidence.
Run:

```sh
uv run python -m unittest tests.applications.adapters.test_zen -v
uv run python -m unittest tests.applications.adapters.appserver.test_requests -v
uv run python -m unittest tests.applications.adapters.appserver.test_gateway_request_integration -v
uv run python -m unittest tests.gateway.test_vertical_slice -v
```

Later physical movement must run full discovery, every AGENTS gate, AgentKit,
and clean-wheel smoke.

## Authority

- [Zen design](design.md)
- [Zen transition page](../../../application-adapters/adapters/zen.md)
- [Adapter testing context](../../../application-adapters/testing.md)

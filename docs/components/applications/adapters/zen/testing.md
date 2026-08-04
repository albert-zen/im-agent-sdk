# Zen Application adapter testing

## Current evidence

Zen is covered by the shared App Server suites and vertical evidence:

- `tests/test_appserver_client.py` and `tests/test_appserver_input.py` prove
  native start, profile/options, connection reset, and no Codex steer policy;
- `tests/test_appserver_requests.py` proves command-approval mapping and
  explicit unsupported request methods;
- `tests/applications/presentation/test_artifact_materialization.py` proves
  the optional shared artifact position without adding Codex live activity;
- `tests/test_gateway_vertical_slice.py` proves Zen and Codex remain distinct
  Application kinds and native outcome behavior.

The tests must keep Zen's truthful `started/create_new` result, native history
recovery, request cardinality/bounds, and explicit unsupported capability. A
shared transport fixture is not evidence for a Codex-only Zen feature.

## Target evidence and verification

The target mirrored suite is `tests/applications/adapters/test_zen.py`;
existing integration tests remain affected evidence during migration. Run:

```sh
uv run python -m unittest tests.test_appserver_input -v
uv run python -m unittest tests.test_appserver_requests -v
uv run python -m unittest tests.test_gateway_vertical_slice -v
```

Later physical movement must run full discovery, every AGENTS gate, AgentKit,
and clean-wheel smoke.

## Authority

- [Zen design](design.md)
- [Zen transition page](../../../application-adapters/adapters/zen.md)
- [Adapter testing context](../../../application-adapters/testing.md)

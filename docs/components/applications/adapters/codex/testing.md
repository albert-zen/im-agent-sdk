# Codex Application adapter testing

## Current evidence

Codex behavior is currently covered by the App Server suites:

- `tests/test_appserver_client.py` and `tests/test_appserver_transport.py` for
  client composition, epochs, lanes, and fences;
- `tests/test_appserver_input.py` for start/steer policy, pre-dispatch races,
  local-image epochs, replacement, and unsupported files;
- `tests/test_appserver_mapping.py` for resource/item normalization;
- `tests/test_appserver_requests.py` for Codex request/response lifecycle;
- `tests/applications/presentation/test_live_activity.py` and
  `test_artifact_materialization.py` for optional typed A1 positions;
- Gateway vertical and conformance suites for the public Application seam.

Evidence must retain Codex-only steer/live behavior, exact native ordering,
stable message/item identity, bounded presentation/materialization, honest
unknown dispatch outcomes, and recovery through authoritative history. No
test should route raw native events or Gateway policy through the adapter.

## Target evidence and verification

The target mirrored suite is `tests/applications/adapters/test_codex.py`;
existing integration/vertical tests remain affected evidence until the test
tree is mechanically mirrored. Run the current focused set with:

```sh
uv run python -m unittest tests.test_appserver_client -v
uv run python -m unittest tests.test_appserver_input -v
uv run python -m unittest tests.test_appserver_mapping -v
uv run python -m unittest tests.test_appserver_requests -v
```

Later physical movement must run full unittest discovery, all AGENTS gates,
AgentKit checks, and clean-wheel smoke.

## Authority

- [Codex design](design.md)
- [Codex transition page](../../../application-adapters/adapters/codex.md)
- [Adapter testing context](../../../application-adapters/testing.md)

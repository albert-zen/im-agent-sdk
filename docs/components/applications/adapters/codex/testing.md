# Codex Application adapter testing

## Current evidence

Codex behavior is currently covered by the App Server suites:

- `tests/applications/adapters/appserver/test_client.py` and `tests/applications/adapters/appserver/test_transport_lifecycle.py` for
  client composition, epochs, lanes, and fences;
- `tests/applications/adapters/test_codex.py` for start/steer policy,
  pre-dispatch races, local-image epochs, replacement, and unsupported files;
- `tests/applications/adapters/appserver/test_mapping.py` for resource/item normalization;
- `tests/applications/adapters/appserver/test_requests.py` for the Codex
  request/response lifecycle; the retained Gateway integration case remains
  in `tests/applications/adapters/appserver/test_gateway_request_integration.py`;
- `tests/applications/presentation/test_live_activity.py` and
  `test_artifact_materialization.py` for optional typed A1 positions;
- Gateway vertical and conformance suites for the public Application seam.

Evidence must retain Codex-only steer/live behavior, exact native ordering,
stable message/item identity, bounded presentation/materialization, honest
unknown dispatch outcomes, and recovery through authoritative history. No
test should route raw native events or Gateway policy through the adapter.

The focused suite also proves stable workspace list/get across reconstruction,
changed-root fingerprint evidence, required Project ancestry in every mapped
Thread/event/history/request, and typed unsupported Project creation with no
native call or workspace mutation.
It also proves that missing or foreign native `cwd` evidence is filtered from
listing and blocks reads, input/control mutations, notifications, request
opening, response writeback, and resolution publication.
Transient notification verification failure produces an explicit recovery gap,
and request publication/cache maintenance cannot turn an already successful
native response into a local failure through a second scope read.
The suite also proves a just-created scope-valid Thread returns an empty
pre-input history/catch-up baseline without calling a rejecting native turn
list; first input also skips the unavailable turn-bearing active-Turn probe
while retaining the ordinary scope read. Foreground public Gateway binding then accepts first input and delivers
its first output once. The first dispatch fence or turn-bearing native event
retires that bounded evidence, while a thread-only creation/status notification
does not. Non-created Threads, bounded evidence eviction,
adapter reconstruction, checkpointed recovery, and unrelated native history
failures remain explicit.

## Target evidence and verification

The target owner suite is `tests/applications/adapters/test_codex.py`; the
retained `tests/applications/adapters/appserver/test_input_integration.py` case remains affected cross-adapter
evidence. Run the focused set with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_client -v
uv run python -m unittest tests.applications.adapters.test_codex -v
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_mapping -v
uv run python -m unittest tests.applications.adapters.appserver.test_requests -v
uv run python -m unittest tests.applications.adapters.appserver.test_gateway_request_integration -v
```

Later physical movement must run full unittest discovery, all AGENTS gates,
AgentKit checks, and clean-wheel smoke.

## Authority

- [Codex design](design.md)
- [Codex transition page](../../../application-adapters/adapters/codex.md)
- [Adapter testing context](../../../application-adapters/testing.md)

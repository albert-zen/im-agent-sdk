# Agent Application adapters testing

Every adapter should prove:

- instance-scoped Project/Thread references;
- managed/flat/fixed project shape;
- create/read/list and side-effect-free pagination;
- Thread lookup independent from native activation;
- actual archive/permanent deletion semantics;
- stable client-message ID round-trip;
- concurrent input returns the distinct native `AcceptedTurn` identity for
  each call;
- cancellation, timeout, and response loss after native input dispatch report
  unknown rather than a retryable pre-dispatch failure;
- canonical user and Agent message events;
- multiple completed messages before an explicit terminal Turn event;
- fan-out-safe subscriptions;
- authoritative snapshot/history plus live reconciliation;
- honest replay, cursor, sequence, attachment, request, and unsupported
  capabilities.
- request open/respond/resolve wire mapping from a native protocol fixture;
- stale response after transport reset when no pending snapshot exists;
- native resolution racing response writeback remains resolved;
- bounded terminal diagnostics emit stale before evicting an unresolved
  responded request's final Thread/Turn routing scope;
- duplicate response and unsupported request shapes fail explicitly rather
  than selecting approval/sandbox policy.

Codex request mapping is covered by `test_appserver_requests.py`, including
permission response fidelity, secret sensitivity, transport-epoch staleness,
JSON-RPC error classification, terminal-cache bounds, and adversarial
Markdown fields. Zen and T3 currently assert `unsupported`; a second real
adapter still requires its own native request/response evidence before Issue
#10 can be fully accepted.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_adapter_contracts.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_client.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_transport.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_requests.py" -v
uv run python -m unittest discover -s tests -p "test_gateway_vertical_slice.py" -v
uv run python -m unittest discover -s tests -p "test_recovery.py" -v
```

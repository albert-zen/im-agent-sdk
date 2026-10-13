# App Server client testing

## Current evidence

The focused client evidence now lives in
`tests/applications/adapters/appserver/test_client.py`
(`AppServerClientTests`); the client/lifecycle portions of
`tests/applications/adapters/appserver/test_transport_lifecycle.py` remain cross-component evidence. It covers:

- stdio and external WebSocket composition, target parsing, supervisor child
  lifecycle, and local-image capability verification;
- one transport-owned frame limit is validated by the factory/client and
  forwarded unchanged to stdio, supplied/custom WebSocket wrappers, and the
  default TCP/Unix WebSocket connector `max_size`;
- bounded request timeout/retry and explicit connection errors;
- notification and server-request lane capacity, reset, and handler failure;
- immutable dispatch-position ordering across both lanes and response fences;
- dispatch preserves an exact 64-key native `params` mapping for both lanes,
  rejects the 65th key in mapping, and keeps client connection context outside
  that native payload;
- connection-epoch reset, stale transport-bound calls, and reconnect behavior;
- oversized transport input reaches no response or callback dispatch, resets
  the poisoned connection, and permits only a fresh later connection/epoch;
- bounded diagnostic facts without endpoint/path/native-payload leakage.
- protocol, stderr, reconnect, supervisor, and health logging have one fixed
  bounded redacted `appserver.debug.v1` shape; arbitrary native or health
  values are never forwarded unchanged.

The test fixtures exercise the typed client and transport seams directly; they
do not stand up Gateway or introduce a second subscription.

## Target evidence

The target suite is
`tests/applications/adapters/appserver/test_client.py`. It preserves the
current assertions and adds no alternate client implementation. Transport
framing remains in the sibling transport suite. Client tests continue to
prove that a later response cannot widen its immutable admission fence, that
the fence resets with the connection epoch, and that an ambiguous native
mutation is not retry permission.

## Verification

Run the focused current suites with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_client -v
uv run python -m unittest tests.applications.adapters.appserver.test_transport_lifecycle -v
```

The full Applications adapter block also requires the repository unittest,
compile, schema, documentation-link, ruff, format, pyright, component-map,
AgentKit, and clean-wheel checks recorded by the parent navigation and
`AGENTS.md`.

## Authority

- [Client design](design.md)
- [App Server block](../README.md)

Input rejection tests allowlist exact method/code pairs and preserve generic
errors for mismatched methods, native codes, or JSON-RPC codes. Real Zen
interoperability verifies busy and expected-Turn rejection without append.

Wire-level start/steer regressions reject string/fractional/coerced error codes,
non-string messages, contradictory result/error payloads, malformed version/ID
fields, and malformed overload responses. Valid native negative and overload
responses retain their existing behavior, including omitted JSON-RPC version.

`tests/applications/adapters/test_zen_queue_lifecycle.py` also exercises the
production client's native and CAS observation owners: generic and explicit
entrypoints, distinct-Thread progress, fresh options, cancelled queued waiters,
post-dispatch caller cancellation, timeout/reset, and repeatedly cancelled close
while the connection lock is held. Actual authenticated Core coverage lives in
`test_zen_queue_native_lifecycle.py` and the public queued Gateway native fixture.

`test_resume_ownership.py` uses the public client/supervisor/WebSocket-factory
boundary with an immediately answering synthetic wire. It covers every public
CAS resume entrypoint inside a ready handler, an outer first resume triggering
same-Thread and different-Thread ready callbacks, actual ready-handler RPC
timeout, initializer cancellation, and close/reset after readiness while waiting
for a Thread lock. No timing yield is used to make a deadlock pass; completed
wire responses and explicit barriers establish progress and no cross-epoch resend.

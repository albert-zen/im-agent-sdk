# Agent Application adapters testing

Every adapter should prove:

- instance-scoped Project/Thread references;
- managed/flat/fixed project shape;
- create/read/list and side-effect-free pagination;
- Thread lookup independent from native activation;
- actual archive/permanent deletion semantics;
- stable client-message ID round-trip;
- every adapter accepts the default continuation preference, invokes the typed
  pre-dispatch hook exactly once, and returns the truthful `started` or
  `steered` result/correlation policy;
- concurrent starts return distinct native `AcceptedTurn` identities;
- cancellation, timeout, and response loss after native input dispatch report
  unknown rather than a retryable pre-dispatch failure;
- a successful App Server input response without a native Turn identity also
  reports unknown rather than authorizing redelivery;
- App Server steer has the same dispatch-unknown boundary as start;
- Codex continuation is enabled by default, explicit start-new bypasses active
  discovery, deployment disablement remains available, and native Turn
  completion/replacement between read and steer never causes an unconditional
  start or a second state read that can mask the primary native error;
- Zen and T3 return `started/create_new` for the common default preference
  until their own native protocols prove an equivalent continuation mutation;
- local-image start and steer pass the verified connection epoch, including a
  positive shared-base Zen case, while generic files remain explicit
  unsupported without a downstream exposure/encoding policy;
- canonical user and Agent message events;
- recoverable activity/tool items use completed messages, while live-only
  presentation uses created messages with bounded namespaced Metadata;
- the optional App Server presentation hook observes typed candidates in live
  and authoritative order, can enrich a final message or emit a terminal
  fallback, and is inert when absent;
- multiple completed messages before an explicit terminal Turn event;
- fan-out-safe subscriptions;
- bounded fan-out overflow isolates the slow subscriber and leaves unrelated
  Threads/subscribers progressing;
- authoritative snapshot/history plus live reconciliation;
- honest replay, cursor, sequence, attachment, request, and unsupported
  capabilities.
- request open/respond/resolve wire mapping from a native protocol fixture;
- stale response after transport reset when no pending snapshot exists;
- native resolution racing response writeback remains resolved;
- bounded terminal diagnostics emit stale before evicting an unresolved
  responded request's final Thread/Turn routing scope;
- App Server notification/server-request queue overflow remains explicit and
  connection-scoped; its reset becomes an Application observation gap and
  Gateway authoritative recovery, while T3 subscription overflow does not leak
  polling work;
- App Server diagnostics cover ready/reconnect epochs and both dispatch-lane
  overflows without exposing endpoints, paths, native IDs, or error text;
- T3 diagnostics expose no synthetic long-lived connection, proving the
  optional provider is not a Codex-specific Core requirement;
- App Server callback positions preserve wire admission order across its
  independent notification/request lanes, fence a later response, and restart
  at the next connection epoch without claiming replay semantics; frames read
  after a response cannot widen that response's immutable fence;
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

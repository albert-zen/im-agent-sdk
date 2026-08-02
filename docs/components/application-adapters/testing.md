# Agent Application adapters testing

Every adapter should prove:

- instance-scoped Project/Thread references;
- managed/flat/fixed project shape;
- create/read/list and side-effect-free pagination;
- App Server Thread creation preserves default behavior, isolates caller- and
  client-owned native option objects, and rejects attempts to replace
  adapter-owned `cwd` or pass colliding snake/camel-case native fields;
- concrete per-call App Server creation can select a native profile without
  mutating the configured default or widening the common `CreateThread`;
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
- multiple completed messages before an explicit terminal Turn event;
- fan-out-safe subscriptions;
- bounded fan-out overflow isolates the slow subscriber and leaves unrelated
  Threads/subscribers progressing;
- authoritative snapshot/history plus live reconciliation;
- an ADR 0015 A1 implementation receives bounded typed facts off the socket
  read path, preserves native item/Turn ordering, produces the same recoverable
  association in history, and leaves default adapters unchanged when absent;
- Codex live activity presentation receives no raw payload/client, uses stable
  native event identity when present, emits `message.created`, remains ordered
  with surrounding notifications, deduplicates delivery by event identity,
  and never advances a completion checkpoint;
- T3 activity presentation uses a distinct typed fact shape, separate message
  and activity identity domains, one ordered polling lane, and the same stable
  association in live polling, catch-up, and history;
- configured T3 input returns native acceptance before presenter work, live
  message/activity candidates retain history ordering, and a polling presenter
  failure produces an explicit recoverable gap rather than a stalled observer;
- a missing T3 activity cursor or more unseen activities than the finite live
  window produces a recovery gap before any later checkpoint can advance;
- active T3 poll state cannot be evicted, capacity exhaustion is explicit, and
  partial-attempt activity deduplication remains within the configured bound;
- structured selected values and Codex diff path entries cannot cross the
  typed facts;
- A1 input/fact collections and text output are finite; timeout, cancellation,
  cancellation overrun, capacity exhaustion, malformed output, reconnect/gap,
  duplicate notification, terminal Turn, and shutdown remain explicit and
  bounded without storing facts or rendered output;
- absent presenters preserve exact Zen/T3/Codex event, history, polling, and
  diagnostics behavior, and Zen does not gain Codex live mapping by sharing
  the App Server transport;
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

Codex and Zen request mapping is covered by `test_appserver_requests.py`, including
permission response fidelity, secret sensitivity, transport-epoch staleness,
JSON-RPC error classification, terminal-cache bounds, and adversarial
Markdown fields. Zen's command approval round trip is backed by the IMZen
native App Server integration and a Gateway projection/response vertical
slice, while unevidenced Zen request kinds fail explicitly. T3 remains
`unsupported` until its own native request/response evidence exists.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_adapter_contracts.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_client.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_transport.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_requests.py" -v
uv run python -m unittest discover -s tests -p "test_gateway_vertical_slice.py" -v
uv run python -m unittest discover -s tests -p "test_recovery.py" -v
```

# Gateway testing

## Critical scenarios

- resource listing never mutates Conversation binding;
- binding a Thread never activates native Application state;
- a Controller and a native interaction use the same typed action surface;
- Slash and native-action request responses create the same Application
  `request.respond` operation;
- request responses require an actual successful destination correlation and
  distinguish unauthorized destination, duplicate/responded, resolved, and
  stale outcomes;
- request-scoped concurrency converges on the native first writer across
  multiple destinations;
- a slow destination completing after the first writer inherits `responded`
  instead of creating a new `open` route;
- a no-snapshot Application request emitted during `start()` is observed
  after restored subscriptions are installed and remains answerable;
- binding selection changes never retarget a previously delivered request;
- live observation is established before synchronous native notifications;
- duplicate inbound messages and duplicate outbound items are idempotent;
- correlation or projection-drain failure after `AcceptedTurn` keeps inbound
  idempotency terminal, while a failure before native acceptance remains
  retryable;
- cancellation or response loss after native input dispatch and failure of a
  post-acceptance terminal write remain sticky across redelivery/restart;
- stale outbound leases are reclaimed, while an already-accepted durable
  submission converges without sending a second Channel message;
- proactive delivery rejects unscoped targets before staging or routing;
- explicit Conversation and policy-resolved Thread targets both work;
- route snapshots remain pinned across route movement and restart;
- concurrent reuse of one delivery ID sends once, while mismatched reuse is a
  conflict;
- an external principal named like the Gateway-internal principal still has a
  distinct SDK-controlled submission namespace;
- Thread-targeted results redact resolved Conversation IDs and aggregate or
  per-item native message IDs;
- public proactive `LocalPath` input requires a stable SHA-256 digest;
- unsupported attachment source/count/size rejects every destination before
  side effects;
- per-artifact failures and multi-destination failures remain typed partial
  results, and ambiguous outcomes are never resent automatically;
- the optional JSON ingress removes staged bytes and the reference CLI refuses
  non-loopback endpoints;
- slow Channel delivery does not await/block the native event producer;
- startup message/operation admission is FIFO and bounded, overflow fails
  startup, and teardown leaves no owned work running;
- callbacks racing failed startup or shutdown are explicitly rejected rather
  than reaching a stopping Application;
- Application event overflow is visible in bounded health and recovers only
  the affected Thread without restarting unrelated Applications;
- concurrent Threads and Turns do not steal events or routes;
- restart rebuilds projection from routes plus bounded authoritative history;
- `AcceptedTurn` reply correlation is per Turn and destination-safe;
- foreground restart/switch reclaims and restores the correct worker;
- Application subscription failure self-recovers while one destination
  failure remains isolated and visible;
- attachment and delivery failures stay explicit.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_gateway*.py" -v
uv run python -m unittest discover -s tests -p "test_projection*.py" -v
uv run python -m unittest discover -s tests -p "test_event_fanout.py" -v
uv run python -m unittest tests.test_proactive_delivery tests.test_delivery_ingress -v
```

Also run the full adapter contract suite after changing a Gateway-facing port.

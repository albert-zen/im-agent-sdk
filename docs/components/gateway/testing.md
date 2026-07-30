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
- slow Channel delivery does not await/block the native event producer, while
  current unbounded queue growth remains a known limitation;
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
```

Also run the full adapter contract suite after changing a Gateway-facing port.

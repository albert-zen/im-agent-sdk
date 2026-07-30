# Gateway testing

## Critical scenarios

- resource listing never mutates Conversation binding;
- binding a Thread never activates native Application state;
- a Controller and a native interaction use the same typed action surface;
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

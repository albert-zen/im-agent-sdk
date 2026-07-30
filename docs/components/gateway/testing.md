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
- restart rebuilds projection from routes plus authoritative history;
- attachment and delivery failures stay explicit.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_gateway_operations \
  tests.test_gateway_vertical_slice \
  tests.test_projection_routing \
  tests.test_event_fanout -v
```

Also run the full adapter contract suite after changing a Gateway-facing port.

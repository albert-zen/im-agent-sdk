# Gateway concurrency testing

Focused evidence lives in `tests/gateway/test_concurrency.py` and must prove:

- the canonical classes are defined once in `imagent.gateway.concurrency` and
  the historical `imagent.keyed_locks` module is absent in a clean process;
- the module has no routing, delivery, persistence, Application, Channel, or
  other Gateway runtime dependency and is not re-exported by the Gateway
  facade;
- an absent bound is accepted, while a configured bound accepts only positive
  non-boolean integers; every consumer using the absent primitive bound proves
  a finite enclosing admission bound;
- equal stable keys share one entry, including at capacity, while a distinct
  key fails explicitly without evicting an owner or waiter;
- independent keys can progress concurrently;
- owner failure, owner cancellation, and waiter cancellation release usage
  exactly once and remove only the final matching entry;
- a cancelled waiter cannot unlock the current owner or split later same-key
  serialization; and
- completed keys do not accumulate and a released slot can admit a later key.

Behavior-specific suites remain with their consumers:

- `tests/gateway/routing/test_operations.py` and
  `tests/gateway/test_operations_integration.py` cover the Conversation key, capacity
  mapping, pre-side-effect rejection, and inbound claim behavior;
- `tests/gateway/delivery/test_coordination.py` covers destination FIFO,
  independent progress, retry-delay ordering, joined cancellation, and that
  active destination keys cannot exceed finite reserved `max_pending` work;
- `tests/gateway/delivery/test_proactive_ingress.py` covers exact validated
  delivery-ID identity, finite ingress capacity, pre-authorization/staging
  ordering, fixed response mapping, and restart-empty coordination; and
- interactive-request integration tests cover RequestRef contender
  serialization, including its outer finite Conversation-operation admission,
  without turning the primitive into request authority.

Run the focused owner and consumers before the complete repository gates:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_concurrency tests.gateway.routing.test_operations tests.gateway.delivery.test_coordination tests.gateway.delivery.test_proactive_ingress -v
PYTHONPATH=src uv run python -m unittest discover -s tests -v
```

Because this removes an internal module path, also run component-map/import
checks and the six-case clean-wheel smoke.

# Gateway input dispatch testing

Current evidence is in `tests/test_gateway_vertical_slice.py`,
`tests/test_projection_hardening.py`, `tests/test_recovery.py`, and Application
adapter conformance. The target mirrored suite is
`tests/gateway/input/test_dispatch.py`.

Tests must prove:

- stable client message identity and one input attempt per admitted message;
- Conversation serialization, finite active-key capacity, same-key waiters,
  cancellation cleanup, and no side effects on capacity rejection;
- default prefer-active-Turn behavior, native steer when supported, and truthful
  `started` fallback when unsupported, never fabricated steer or hidden queue;
- exactly one pre-dispatch fact with matching Thread/client ID and legal
  disposition/correlation fields;
- pre-fence failure releases; post-fence cancellation/response loss remains
  `side_effect_started`; accepted input remains terminal across later failures;
- started/steered reply correlation remains Turn- and destination-safe across
  restart and two Conversations;
- bounded acceptance buffering closes synchronous event races without a second
  Application subscription.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.test_gateway_vertical_slice tests.test_projection_hardening tests.test_recovery -v
```

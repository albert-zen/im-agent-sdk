# Gateway input dispatch testing

Focused evidence is `tests/gateway/input/test_dispatch.py` and
`tests/gateway/input/test_policy_free_ordinary_input.py`; vertical
integration remains in `tests/gateway/test_vertical_slice.py`,
`tests/gateway/projection/test_hardening.py`, `tests/gateway/projection/test_recovery.py`,
and Application adapter conformance.

Tests must prove:

- stable client message identity from only stable Conversation/native message
  identity, exact public `MissingBindingError` and
  `imagent.gateway.input` facade identity, and absence of the
  historical `imagent.contracts.validators` module/validator alias, including a
  clean-process failed import;
- one input attempt per admitted message while the Gateway-owned Conversation
  serializer continues to cover same-key waiters, cancellation cleanup, and
  capacity rejection before effects;
- no binding, Application-only binding, and Project-only binding return the
  exact `MissingBindingError`/`missing_binding` classification without binding,
  projection-route, resource, native-input, or delivery mutation;
- one Application is never selected implicitly, and no Project, Thread, CWD,
  or onboarding action is derived from Message content;
- a Controller can explicitly create/select a Project and create/bind a Thread
  through its scoped `ConversationActions`, return `None`, and send the same
  Message through the one canonical dispatch runtime exactly once;
- two Conversations and foreign Project/Thread references cannot cross action,
  binding, route, or dispatch scope;
- default prefer-active-Turn behavior, native steer when supported, and truthful
  `started` fallback when unsupported, never fabricated steer or hidden queue;
- exactly one pre-dispatch fact with matching Thread/client ID and legal
  disposition/correlation fields;
- a returned `AcceptedTurn` that mismatches the authorized disposition or
  correlation fails explicitly after the native side-effect boundary and never
  retargets a Turn;
- pre-native-side-effect-fence failure releases; post-native-fence
  cancellation/response loss remains `side_effect_started`; accepted input
  remains terminal across later failures;
- started/steered reply correlation remains Turn- and destination-safe across
  restart and two Conversations;
- bounded acceptance buffering closes synchronous event races without a second
  Application subscription; event order drains only after correlation is
  resolved, overflow and mid-drain applier failure report authoritative-recovery
  gaps, accepted input remains terminal, and terminal observation worker cleanup
  cannot clear a pending acceptance-ordering gate or leave a stale recovery
  cancellation marker; a failed recovery schedule remains secondary to the
  original ordered-event failure or cancellation;
- constructor-injected typed collaborators cover correlation and event
  application without a generic hook, service locator, global registry, or
  `Any` seam.

Focused registry mechanics and old-module absence live in
`tests/gateway/test_concurrency.py`; dispatch integration retains the stable
Conversation key, configured bound, pre-effect rejection, and claim-transition
evidence.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.input.test_dispatch tests.gateway.input.test_policy_free_ordinary_input tests.gateway.test_vertical_slice tests.gateway.projection.test_hardening tests.gateway.projection.test_recovery -v
```

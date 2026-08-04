# Common commands testing

Required scenarios:

- the common set registers through the same explicit frozen registry as a
  neutral consumer command;
- consumers can omit a common definition and register a product command, but
  duplicate/shadowed registration fails;
- help, list/select, create/delete/status, catch-up/history, and typed request
  response preserve current behavior;
- non-Slash input remains unconsumed and trailing input context is ignored by
  command parsing;
- listing does not mutate binding, selecting a Thread does not activate native
  UI state, and observation remains an explicit typed action;
- two Conversations may bind/observe one Thread, while switching one under
  `foreground_only` removes only that Conversation's old projection authority;
- operation IDs are stable across replay of the same complete scoped inbound
  identity and cannot collide when two Conversations reuse a native message ID;
- read-only common definitions remain replay-safe, while mutating definitions
  cross the durable effect fence before handler invocation;
- unsupported capability, stale reference, invalid arguments, binding
  conflict, and incompatible typed result are explicit bounded failures;
- Project/Thread list, view-cache capacity/lifetime, history/catch-up content,
  output text, handler concurrency, and lifetime are finite;
- view eviction never guesses a numeric selection;
- presentation or Channel failure after a successful mutation does not repeat
  the mutation;
- restart discards views and rebuilds from Gateway/Application authority;
- no product-only command or concrete Application client enters the common
  module.
- clean-process import order proves the controller facade does not load this
  module until one of its two finite common-command exports is requested, and
  that both cached values have exact owner identity.

Focused validation currently includes:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.controllers.test_common_commands \
  tests.interaction.controllers.test_registry \
  tests.interaction.controllers.test_optional_controller \
  tests.test_gateway_operations \
  tests.test_gateway_vertical_slice \
  tests.test_projection_routing -v
```

`tests/interaction/controllers/test_common_commands.py` owns registry
composition and bounded common-command tests. The optional
Controller/common-command parity suite is now owned by
`tests/interaction/controllers/test_optional_controller.py`; the remaining
root Gateway/Application parity suites are separate migration slices.
Formal-facade identity and clean-process/import-order tests prove every public
common-command object comes from its Interaction owner and the removed
historical package cannot be imported.

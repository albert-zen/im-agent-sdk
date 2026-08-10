# Controller contract design

## Purpose and ownership

The optional `InboundController` recognizes product UX after verified Channel
admission and before ordinary input dispatch. Its handler receives the exact
`ConversationActions` instance already frozen to the inbound Conversation and
authenticated actor. The Controller contract owns only invocation/result and
lifecycle shape; `gateway.actions` owns the capabilities.

`handle(message, actions)` returns `None` to continue ordinary input, or a
finite tuple of scoped `OutboundMessage` values to consume it. A Controller
cannot replace the inbound envelope or return output for another Conversation.

There is no `ControllerActions`, `CommandHandlerActions`, generic Application
operation executor, generic Gateway operation executor, or binding lookup that
accepts a Conversation. Those pre-v1 values are removed without aliases.

The registry uses the surface's private inbound-fence entry only to preserve
the already-owned inbound claim boundary before an effectful handler. Product
handlers receive the same object identity, not a narrowed wrapper, repository,
claim, or service locator. Durable action replay is owned by the action effect
executor, not by the Controller.

Ordinary input composes this surface only from one lease-bound coherent B store
session and B's `StoreBackedGatewayEffectExecutor`; it never casts or
substitutes the old generic-operation wrapper. If coherent action wiring is
unavailable, Controller configuration fails before input. A Controller may
implement optional onboarding with `create_and_select_project` and
`create_and_bind_thread`, then return `None` so the exact original Message is
resolved and sent by the same ordinary-input dispatcher. Gateway itself never
performs that policy, guesses a CWD, or creates a second dispatch route.

`ControllerLifecycle` remains an optional structural capability for startup
validation and bounded shutdown. An unfrozen registry fails before input.

## State and recovery

The contract persists nothing. Returning or delivering presentation never
reauthorizes a completed handler effect. Unknown action outcomes are never
automatically retried. With no Controller, Slash-looking text follows the same
ordinary input path as any other content.

Canonical code is
`src/imagent/interaction/controllers/contract.py`; the only public values from
this leaf are `InboundController`, `CommandInvocationFacts`,
`ControllerLifecycle`, and their fixed result shape.

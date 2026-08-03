# Common commands design

## Purpose

Common commands provide optional portable UX over typed Controller actions for
Application/Project/Thread navigation, binding, observation, status, catch-up,
history, and typed request response.

## Ownership

This leaf owns common command definitions, portable bounded argument parsing,
discardable selection views, and default Markdown result presentation. It does
not own product commands, native Application policy, Gateway mutations,
request authorization, Channel Markdown escaping/segmentation, or product
configuration such as Codex CWD/profile/Full Access/credits.

## Composition

Every common command is a `CommandDefinition` registered explicitly into the
same local `CommandRegistry` used by consumer definitions. Consumers may
select the common set they need. They do not override registered definitions;
for product-specific `/new` UX, a consumer omits the common `/new` definition
and registers its own handler while still using public `ControllerActions` for
shared create/bind/observe semantics.

`register_common_commands(registry, ...)` performs that explicit composition.
`SlashController` calls the same function on a private registry and freezes it
for the default all-common-command experience; it is not a second dispatcher.

Current common behavior covers help, Application/Project/Thread listing and
selection, create/delete/status, catch-up/history, and typed approval/input
responses. Buttons and other Channel-native interactions call the same typed
actions without manufacturing Slash text.

## Operation semantics

Listing never changes selection. Binding a Thread does not activate it in a
native UI. Common command composition preserves the separation among input
binding, native activation, and output observation. Under `foreground_only`,
Gateway's binding operation remains the authority that prepares the matching
route and removes projection authority after a Conversation switches; other
current Conversations observing the old Thread are unaffected.

Target operation IDs are stable functions of the complete scoped inbound
identity—Channel instance, Conversation, native message—and typed operation
kind, never presentation text or timestamps. Unsupported
capabilities and typed failures remain explicit and user-safe. Common command
code does not reach concrete Application clients.

Each common definition declares its closed execution-safety class. Pure
listing, help, status, catch-up, and history handlers are replay-safe/read-only.
Create, bind/select, delete, observe, activate, and request-response mutations
are effectful and cross the Controller runtime's durable effect fence before
their handler is invoked. No handler or presenter receives claim authority.

## Presentation and bounds

The portable presenter emits bounded `OutboundMessage` content and leaves
native escaping, segmentation, delivery, and receipts to the Channel. Thread
and Project selection views are process-local, capacity/lifetime bounded, and
discardable; authoritative selection remains in Gateway binding state and
native resources remain in the Application.

Only the first non-empty input line is command grammar. Arguments, list sizes,
history/catch-up items, output text, handler concurrency, and handler lifetime
obey the registry/operation bounds. A cache miss after eviction causes an
explicit refresh or stable-ID requirement, never a guessed selection.

When no Application is selected and exactly one is registered, read-only
commands use that Application only as an ephemeral query context; they do not
write a binding. An effectful selection/create flow may establish the binding
through the normal typed Gateway action after crossing the fence.

## Failure and recovery

Common handlers do not automatically retry. They preserve the registry's
known-pre-effect versus unknown-outcome classification and the called typed
action's result. Failure delivering presentation cannot repeat a completed
mutation. Restart discards views and reconstructs behavior through bindings
and authoritative Application reads; there is no command transcript or
durable Controller spool.

## Implementation slice

The registry conversion replaces fixed dispatch with explicit common
definitions, namespaces operation IDs by the complete stable inbound identity,
and adds finite capacity/lifetime bounds to both selection views while
preserving command presentation and typed-action behavior. The later
mechanical move places the single common implementation under
`imagent.interaction.controllers` and removes the old internal path.

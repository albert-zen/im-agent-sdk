# Common commands design

## Purpose and composition

Common commands are optional `CommandDefinition` values over the same scoped
`ConversationActions` surface used by native UI actions. A consumer calls
`include_common_commands(registry, names=...)` on one local registry, adds
product handlers, and freezes it. There is no default `SlashController`,
global registration, import-time mutation, shadowing, or Slash-specific core
execution path.

## Semantics

Reads never mutate selection. There is no implicit single-Application
onboarding: an unbound Conversation must explicitly select an Application.
Project and Thread selection use primitive Gateway actions and observation is
explicitly separate from native activation.

`/new` calls `create_and_bind_thread`; it never hand-sequences native create
and binding. Deletion calls only the primitive native delete and never silently
clears a binding. Request response calls only the Conversation-authorized
`respond_request`. The SDK supplies no default CWD; project creation requires
an explicit caller value and is not guessed by common command code.
After `/new` receives its created `ThreadRef`, its explicit `observe_thread`
action must reconcile the live projection runtime before the consumed command
returns success. A partial activation is presented as partial; it is never
collapsed into success or an alternate dispatch path.

Every effectful handler derives a stable action ID from admitted Channel,
Conversation, message, canonical command/action, and bounded arguments. It
preserves typed failed, partial, and unknown outcomes and never retries.

Selection views are bounded, expiring, and discardable. They contain only
bounded summaries; binding truth stays in Gateway and native resource truth in
Applications.

Canonical code is `src/imagent/interaction/controllers/common.py` and
`common_presentation.py`. `include_common_commands` is the only public common
composition export.

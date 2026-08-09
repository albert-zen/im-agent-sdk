# Interaction: Channel input and commands

The Interaction layer owns the product-neutral IM boundary. A consumer gives
the Gateway a `ChannelAdapter` that normalizes native input into
`InboundMessage`, accepts `OutboundMessage`, and exposes honest delivery
capabilities. It does not import Gateway orchestration or an Application
implementation.

The reference Channel is [`ReferenceChannel`](../../examples/reference_consumer/interaction.py).
It is a small local transport with the same lifecycle and admission seam as a
real Channel:

- `start()` receives Gateway-owned message and pre-media admission callbacks;
- `emit_text()` creates a stable `InboundMessage` for the local demo;
- `send()` records an `OutboundMessage` and returns a typed accepted receipt;
- `diagnostic_facts()` exposes only fixed, redacted process-local facts.

Outbound records have an explicit positive capacity and fail before append
when it is exhausted. The sample does not evict records or silently grow its
local authority.

The local Channel is useful because it runs the actual Gateway admission,
delivery planning, and routing path. It is not a native IM implementation and
does not add a second admission or delivery path.

## One local command composition

Slash grammar is consumer policy. Build one registry during composition, add
the SDK common command definitions you want, add consumer commands, and freeze
the registry before Gateway startup:

```python
registry = CommandRegistry()
include_common_commands(registry, names=("help",))
registry.register(CommandDefinition("about", handler=about))
registry.freeze()

extensions = GatewayExtensions(controller=registry)
```

The sample’s [`build_command_registry()`](../../examples/reference_consumer/interaction.py)
uses exactly this shape. `/help` is the common command and `/about` is one
neutral consumer command. The registry is local to the composed consumer; it
is not global state, a service locator, or an SDK Core command. A frozen
registry makes startup validation explicit and lets the bounded registry own
handler lifetime and diagnostics.

Every command receives the exact Conversation-scoped `ConversationActions`
surface assembled for its authenticated inbound message. A read-only command
can return bounded `CommandResult` content. Neither command receives a mutable
Gateway context or a raw native callback.

## Boundary rules

Keep these responsibilities separate:

- Channel authentication, sender access, native identity, media preparation,
  and native encoding stay in the Channel or consumer deployment.
- `Message` carries content. Control intent is a typed Application or Gateway
  `Operation`.
- A command can invoke typed actions, but command text never becomes an
  Application transcript item.
- If no Controller is configured, ordinary non-command input still follows the
  existing Gateway path.

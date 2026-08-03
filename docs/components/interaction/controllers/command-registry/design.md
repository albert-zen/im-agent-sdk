# Command registry design

## Purpose

`CommandRegistry` is the explicit local composition surface for SDK common
commands and consumer product commands. It replaces the fixed Slash dispatch
table without creating a global plugin system or moving product behavior into
SDK Core.

## Ownership

This leaf owns:

- one explicit registry instance and its freeze lifecycle;
- strongly typed command definition, invocation, handler, and result values;
- canonical command/alias validation and collision detection;
- bounded parsing, handler admission/lifetime, and result validation;
- fixed failure categories and replay/cancellation rules.

It does not own product services, Application/Gateway implementations,
binding/route policy, a generic pipeline, import-time discovery, durable jobs,
or native retry authority.

## Local composition and freezing

Composition creates a registry, explicitly registers selected SDK common
definitions and consumer definitions, then freezes it before Gateway accepts
input. A decorator is permitted only when it is bound to that concrete
registry instance. Importing a module never mutates registration state.

Registration fails explicitly for invalid definitions and for normalized
collisions among names or aliases. Freezing validates the complete table and
is irreversible. Registration after freeze fails. Independent Gateway
compositions never share mutable registry state.

Consumers choose which common definitions to include. When a product needs a
different `/new` UX, it omits the SDK definition and registers its product
definition; it cannot silently replace or shadow an existing name.

## Typed values and dependencies

A registry composition uses these public values:

- `CommandRegistryLimits` fixes every registry, parser, result, task, and
  common-view bound;
- `CommandArgumentContract` fixes per-command minimum and maximum argument
  counts;
- `CommandExecutionSafety` is the closed `read_only` / `effectful` class;
- `CommandDefinition` binds canonical name, aliases, help metadata, argument
  contract, safety, handler, and handler lifetime;
- `CommandInvocation` carries the complete stable scoped inbound identity,
  canonical command, bounded arguments, actor, and creation time;
- `CommandInvocationFacts` is the read-only structural view accepted only by
  the runtime effect-fence method, avoiding a contract-to-registry dependency;
- `CommandHandlerActions` is the fence-free typed Application/Gateway action
  view available to handlers;
- `CommandResult` carries only bounded `TextContent` plus a closed completed or
  known-failure status; media delivery remains on the normal validated
  Interaction/Gateway path and registry-owned output identity is not handler
  input;
- `CommandRegistryDiagnostics` exposes fixed counters and a fixed last-failure
  category without content, identity, or exception text.

A `CommandDefinition` fixes one canonical name, finite aliases, argument
contract, help/presentation metadata, handler, immutable execution bounds, and
a closed execution-safety classification. Replay-safe/read-only definitions
cannot start external effects. Effectful definitions cross the Gateway-owned
durable effect fence before the handler is invoked; the registry does not hand
that fence or an idempotency repository to the handler.
A `CommandInvocation` contains the stable inbound identity, Conversation,
actor, canonical command, and bounded parsed arguments. It contains no `Any`
context, repository, Gateway, adapter, credential, or raw native event.

The registry alone receives the full `ControllerActions` runtime surface. For
an effectful definition it calls the one-way fence method with that
`CommandInvocation` identity, waits for the durable transition, then invokes
the handler with a separate action view that omits the fence. Read-only
definitions receive only the narrow handler view and do not enter the fence.

A handler returns a closed typed `CommandResult`; it does not manufacture an
`OutboundMessage`, choose Conversation/delivery/reply identity, return
unbounded content, or signal normal control flow through arbitrary exceptions.
The registry creates at most one scoped-hash, fixed-identity response message;
an empty completed result is intentionally silent. Delimiter-bearing Channel,
Conversation, and message IDs cannot alias one another.
Product handlers receive strongly typed product services through constructor
injection. Public binding/Application behavior uses `ControllerActions`.
Product-specific services such as a Codex credits reader remain typed concrete
adapter/client services and do not become public `ApplicationOperation`s
without evidence from two real Applications.

## Bounds

All limits are finite, positive, immutable composition values and fail
validation at startup. They cover at least:

- registry entry and alias count;
- command/alias and argument count/length;
- input line length considered by the parser;
- result item count and text/content size;
- active handler concurrency;
- handler lifetime and cancellation-join lifetime;
- process-local view-cache capacity and lifetime used by common commands.

Capacity exhaustion and timeout are explicit typed failures with bounded,
redacted diagnostics. A slot is reserved atomically before an effectful
invocation waits for its durable fence. Cancelled or timed-out work retains its
capacity slot until it has actually joined; an overrun cannot be hidden by
admitting another handler. Registry work runs on a bounded task/lane, never a
Channel or Application socket reader.

## Parsing and dispatch

The portable Slash parser considers only the first non-empty bounded input
line. It normalizes the command name but preserves typed bounded arguments;
trailing Channel/user context is content, not control syntax. A malformed or
unknown command is a consumed, bounded presentation result only when the
configured Controller grammar recognizes it as command input. Non-command
messages return the Controller's unconsumed marker.

Exactly one definition matches one invocation. The registry never fan-outs a
command to multiple handlers and never falls through to another alias after a
handler starts.

`CommandRegistry.register()` and the instance-bound `CommandRegistry.command()`
decorator are the only registration paths. `freeze()` is irreversible.
`validate_startup()` rejects an unfrozen registry before Gateway accepts input;
`close()` cancels and boundedly joins admitted handler tasks. The registry
itself implements `InboundController`. The default `SlashController` is only a
convenience wrapper around one explicitly populated, frozen registry.

## Failure, replay, and cancellation

The registry performs no automatic handler retries. Lookup and validation are
replay-safe. One process attempt invokes one handler at most once for the
stable message/command identity.

The typed result/outcome boundary distinguishes:

- completed work, including an intentionally silent result;
- known pre-side-effect failure, which may be presented and can be safely
  reclaimed according to the Gateway inbound policy;
- unknown outcome once an external/action side effect may have started, which
  is terminal for automatic handler re-execution;
- cancelled work, classified conservatively as unknown if cancellation raced
  the effect fence.

Stable typed action IDs provide idempotency where the called action supports
it. The registry does not claim exactly-once execution and does not convert an
unknown result into success, retry, queueing, or normal Application input.
Failure while presenting or delivering a completed handler result never
reauthorizes the handler effect.

For an effectful definition, parsing and argument validation finish before the
fence. If the durable fence fails, the handler and its product service are not
called and the failure is known pre-side-effect. Once fence entry begins, a
handler exception, cancellation, timeout, or lost service response is treated
conservatively as unknown unless the invoked typed service proves a terminal
outcome. This sacrifices some retry opportunities rather than risking a
duplicate product or native mutation.

## State and recovery

The frozen definition table and execution counters are process-local. They are
not persisted or replayed as a command log. Durable product state belongs to
the consumer; typed action and inbound idempotency state stay with their
existing owners. Restart rebuilds the same registry through explicit
composition and relies on stable inbound/action identity for convergence.

## Implementation slice

The standalone registry slice replaces the fixed dispatcher, adds the
registry-to-Gateway effect-fence handshake, scopes operation IDs by the full
inbound identity, and bounds common selection views. It preserves
`SlashController` as the default common-command convenience composition while
making `CommandRegistry` the public consumer-composition surface. The later
common-command mechanical move removes the remaining historical internal path.

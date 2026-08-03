# Controller contract design

## Purpose

The Controller contract is the optional Interaction boundary that lets a
consumer recognize product UX in an already verified `InboundMessage` and
invoke the same typed actions used by non-text callers.

## Ownership

This leaf owns:

- `InboundController`;
- the minimum `ControllerActions` protocol;
- the distinction between unconsumed input and consumed bounded output;
- Controller invocation and action-access rules.

It does not own command grammar, product services, binding/route
implementation, native Application state, request authorization, inbound
claim persistence, delivery retry, or another extension position.

## Contract

`InboundController.handle(message, actions)` receives one immutable
`InboundMessage` and a typed `ControllerActions` object. It returns:

- `None` when the message is not consumed, after which Gateway continues to I1
  and normal binding/input dispatch; or
- a finite tuple of `OutboundMessage` values when consumed, including an empty
  tuple for an intentionally silent command.

Every output remains in the inbound Conversation. Gateway validates that
identity and delivers through its one normal delivery path. A Controller
cannot alter the verified inbound envelope and cannot pass transformed content
back to normal input dispatch.

`ControllerActions` exposes only typed Application operations, typed Gateway
operations, and read-only lookup of the current `ConversationBinding`. It is
not Gateway and does not expose repositories, claims, checkpoints, Channel
clients, Application adapters, or extension objects. Gateway supplies an
implementation while holding the Conversation serialization lane.

The target `ControllerActions` also carries one narrow runtime-only method for
the registry to enter the effectful-command fence. It accepts the complete
stable inbound/command invocation identity, is bound to the current owned
inbound claim, may be entered at most once, and only advances that claim to
`side_effect_started`. It cannot inspect, release, complete, or reopen a claim.
Gateway returns from the method only after the durable transition commits.
Identity mismatch, a second entry, or repository failure prevents handler
invocation and fails explicitly.

The one-way method is declared on the Interaction-owned protocol and returns
no Gateway type, so it creates no Interaction import dependency on Gateway
admission or persistence implementation. Gateway implements the protocol and
owns the durable transition behind it.

The registry holds this full runtime action object while it parses, validates,
and dispatches. A handler receives a separate narrow action view containing
only the typed Application/Gateway operations it needs, plus its
constructor-injected product service; the one-way fence method is not present
on that handler view. This is exact Controller/Gateway coordination, not a
generic callback, middleware hook, or service locator.

## Invocation position and absence

Gateway invokes the configured Controller after Channel verification, access,
durable inbound admission, media preparation, and claim refresh, and before I1
or Application input dispatch. It never runs on a Channel socket-read task.
One process attempt invokes the Controller at most once for the stable inbound
identity. With no Controller configured, Slash-looking and ordinary text
follow exactly the same normal input path as before.

## Failure, cancellation, and replay

The Controller itself owns no durable effect record. A confirmed failure or
cancellation before any typed action side effect may release the inbound claim
and permit a later invocation with the same stable message identity. Therefore
parsing, lookup, and pre-effect handler work must be replay-safe.

Typed action operation IDs are stable and must not be derived from text or
time. Before an effectful command handler can call its injected product service
or a mutable typed action, the Controller runtime must cross a narrow
Gateway-owned durable effect fence through the registry's runtime-only action
surface. The handler receives no fence or claim mutation API. A failed fence
does not invoke the handler; once fence entry begins, cancellation is not proof
that the external effect stayed absent.

A Controller or registry must never automatically repeat a handler after an
action reports an unknown outcome. The registry behavior slice must preserve a
typed distinction among completed, known pre-side-effect failure, and unknown
outcome, join cancelled handler work within a finite lifetime, and keep
capacity occupied while overrun work remains live. The current Controller path
does not yet provide this effectful-handler fence; implementing it belongs to
the standalone registry behavior slice, not a mechanical move.

Presentation or Channel delivery failure after a handler effect cannot grant
permission to execute the handler again. Gateway inbound claim transitions
remain authoritative; the Controller cannot complete, release, or reopen a
claim itself.

## State and recovery

The contract persists no state. Handler/product state is consumer-owned;
binding, projection, correlation, and idempotency state remain Gateway-owned;
native resources and execution remain Application-owned. Process-local
Controller view state must be discardable and explicitly capacity/lifetime
bounded by its owning command implementation.

## Physical migration

`src/imagent/interaction/controllers/contract.py` owns the current exact
`ControllerActions` and `InboundController` objects. The historical
`imagent.controllers` package re-exports those same objects as a finite public
migration facade while request presentation and common commands still occupy
that package. Repository runtime code imports the owning Interaction leaf;
there is no second contract implementation.

The accepted registry-only one-way effect fence remains target behavior for
the standalone registry API/behavior slice. This mechanical extraction does
not add the fence, registry, operation-ID scope fix, cache bounds, or any
runtime behavior.

# Gateway input dispatch design

Component ID: `gateway.input.dispatch`

Parent: `gateway.input`

## Purpose

Dispatch turns one verified, unconsumed Conversation input into one truthful
Application input attempt while preserving stable identity and the native
side-effect fence.

## Ownership and flow

This leaf owns per-Conversation serialization at the input handoff, stable
`derive_client_message_id` use, `prefer_active_turn` as the Gateway default,
authorization of the typed `ApplicationInputDispatch` pre-dispatch fact, and
validation of the returned `AcceptedTurn`. Binding mutation, Thread creation,
route preparation, native steer implementation, Application active-Turn
truth, and projection delivery remain with their owning leaves.

After Controller decline and optional I1, Gateway resolves or creates the
bound Thread through typed operations and prepares its projection route. It
derives the client message ID only from stable Conversation and native message
identity, then calls the Application. Immediately before a native mutation,
the adapter must offer exactly one typed dispatch fact through the narrow
pre-dispatch fence:

- `started` requires create-new correlation and no expected Turn;
- `steered` requires preserve-existing correlation and an expected active Turn.

An Application that cannot steer must report `started`; it may not fabricate
steer or silently queue. The returned `AcceptedTurn` must match the authorized
disposition/correlation identity. Raw native events are never exposed to this
leaf or to Controller consumers.

## State, recovery, and bounds

Before the fence, a known failure releases the matching claim. Entering the
fence protects it as `side_effect_started`; lost acceptance or cancellation
after that point is `outcome_unknown` and never authorizes redelivery. A valid
`AcceptedTurn` makes inbound idempotency terminal even if reply-correlation or
buffered projection draining later fails. Started input creates Turn reply
correlation; steered input preserves the existing Turn policy defined by ADR
0012.

Conversation lock cardinality and Turn-acceptance event buffering are finite.
Same-Conversation work serializes; a new key at capacity fails before
Application effects. Consumer work never runs on Channel or Application socket
read paths.

The Gateway operation owner selects the stable Conversation key and maps the
configured capacity failure. Its entry/wait/cancellation mechanics come from
the dependency-neutral `gateway.concurrency` leaf, which owns no input,
binding, claim, or side-effect policy.

## Contracts and structure

The public fact currently exported from `imagent.contracts` is
`derive_client_message_id`; its target owner is `imagent.gateway.input`.
Current implementation spans `src/imagent/gateway/__init__.py`,
`src/imagent/projection_runtime.py`, and contract validation. The target leaf is
`src/imagent/gateway/input/dispatch.py`. Shared projection worker lifecycle
remains an explained split candidate until its own focused move.

Dependencies are the common Application contract/operations, Gateway admission,
binding/route preparation, and projection request/reply correlation.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Gateway aggregate](../../design.md)
- [ADR 0006](../../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0013](../../../../decisions/0013-bounded-application-event-admission.md)

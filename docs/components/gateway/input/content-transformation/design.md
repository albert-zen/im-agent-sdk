# Gateway inbound content transformation design

Component ID: `gateway.input.content-transformation`

Parent: `gateway.input`

## Purpose and position

I1 optionally replaces only the verified content tuple of an unconsumed
`InboundMessage`. It runs after durable duplicate rejection, Conversation
lease refresh, and Controller decline, and before binding-driven Application
creation or native input dispatch. With no transformer, the original content
tuple is used unchanged.

## Ownership and contract

This leaf owns the `InboundContentTransformer` protocol, its bounded invocation
runtime, typed output validation, cancellation/join behavior, and fixed
redacted diagnostics. It receives the frozen original `InboundMessage` and
returns a non-empty tuple containing only supported text or attachment content.

It does not own or change Channel/Application/Conversation identity, native
message ID, sender, reply identity, binding, project or Thread selection,
client message ID, continuation, prefer-active-Turn policy, request/reply
correlation, envelope metadata, or a persisted transformed copy. It exposes no
raw native event or generic stage callback.

The public seam lives at `imagent.gateway.input.InboundContentTransformer`.
The formal `imagent.gateway.InboundContentTransformer` facade re-exports that
exact object. Its implementation is
`src/imagent/gateway/input/content_transformation.py`; orchestration and shared
diagnostic facts remain in the Gateway package root and diagnostics leaf.

## Replay, failure, and capacity

Transformation is deliberately replay-safe. A confirmed pre-dispatch reclaim
or restart may invoke it again because its output is not durable authority.
Timeout, exception, cancellation, invalid output, or capacity rejection occurs
before Application dispatch. Without I2 the matching inbound claim is released
and the error propagates; configured I2 handles the resulting
`pre_acceptance` phase under its independent terminal rule.

Timeout, output-item count, concurrent active tasks, cancellation wait, and
shutdown cleanup are finite `GatewayLimits`. Diagnostics keep only fixed
counters and failure codes for process lifetime; they retain no identity,
content, path, callback output, or exception text. A transformer never runs on
a Channel socket read path.

Dependencies are Interaction messages/media, Gateway admission and diagnostics.
It may not depend on a concrete Channel, Application, Controller product
service, repository, or delivery implementation.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Gateway aggregate](../../design.md)
- [ADR 0011](../../../../decisions/0011-durable-inbound-admission-before-media.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

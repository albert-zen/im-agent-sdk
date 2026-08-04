# Gateway inbound failure presentation design

Component ID: `gateway.input.failure-presentation`

Parent: `gateway.input`

## Purpose

I2 classifies an admitted input failure into one fixed phase and optionally
renders one terminal, idempotent Channel error delivery. It presents failure;
it never grants permission to retry native input.

## Typed seam and identity

This leaf owns `InboundFailurePhase`, `InboundFailurePresenter`, the bounded
render runtime, output validation, and phase-specific claim ordering. The
presenter receives only `pre_acceptance`, `outcome_unknown`, or
`post_acceptance` plus the original Conversation, reply ID, and Gateway-derived
stable delivery ID. It receives no exception, message content, free-form SDK
error text, claim handle, repository, callback, Gateway, or native event.

Output must be one `OutboundMessage` with exactly the fixed Conversation,
reply, and delivery identities. It is non-empty bounded text only; attachments,
arbitrary metadata, changed identity, and unsupported output fail before
Channel effects. Valid output uses the common Coordinator and outbound
idempotency path.

Public I2 contracts are exposed by `imagent.gateway.input`; the established
`imagent.gateway` facade re-exports the exact same objects. Implementation is
`src/imagent/gateway/input/failure_presentation.py`, with Gateway orchestration
in the package root and diagnostic facts owned by
`src/imagent/gateway/diagnostics.py`.

## Claim and replay rules

- With configured I2, `pre_acceptance` completes the owned claim before
  presenter or Channel work. Presenter/delivery failure cannot reauthorize
  native input.
- With no I2, `pre_acceptance` preserves release-and-raise exactly.
- `outcome_unknown` remains protected as `side_effect_started`; I2 never
  completes or releases it.
- `post_acceptance` is terminal before presentation and remains terminal.
- Original cancellation before the dispatch fence releases and does not invent
  presentation; cancellation racing after the fence stays protected.

Stable outbound idempotency may converge a repeated presentation delivery, but
the SDK adds no error transcript, durable presentation job, spool, or outbox.

## Bounds, diagnostics, and dependencies

Render timeout, item/text limits, active task count, cancellation join, and
shutdown cleanup are finite `GatewayLimits`. Diagnostics contain fixed
process-lifetime counters/failure codes only. Presenter work runs in Gateway
workers, never a socket read path.

Dependencies are Interaction messages, the common Application dispatch-error
classification, Gateway admission/dispatch/projection recovery, common delivery
coordination/idempotency, and diagnostics. Product wording and branding remain
consumer policy implemented by constructor-injected typed services.

## Authority

- [Vision](../../../../VISION.md)
- [Architecture](../../../../ARCHITECTURE.md)
- [Persistence design](../../../persistence/design.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

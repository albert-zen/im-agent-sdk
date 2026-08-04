# Request presentation design

## Purpose

Request presentation turns one typed Application approval or structured-input
request into bounded destination-specific portable output without becoming
request, approval, authorization, or transport authority.

## Ownership

This leaf owns `RequestPresenter`, `RequestPresentation`, and the portable
`MarkdownRequestPresenter`. It does not own native pending state, response
authorization, sender admission, Gateway correlation, Channel cards/buttons,
secure secret collection, or Application response dispatch.

## Typed boundary

The presenter receives one bounded typed `InteractiveRequest`, a selected
`ConversationRef`, a Gateway-fixed stable delivery ID, and optional reply
identity. It returns one `RequestPresentation` containing an
`OutboundMessage` with those exact identities plus a boolean indicating
whether that presentation supports response collection.

It receives no raw native event, Application client, Gateway, repository,
credential, or mutable context. The presenter does not deliver output itself.
Gateway uses the existing per-route serialization and creates minimal response
correlation only after the stable delivery is accepted or already completed.

## Markdown and security

All native prompt fields are untrusted. Approval facts use an indented code
block. Headers, labels, descriptions, questions, and other interpolated text
are escaped; command arguments preserve stable Application/request/question
and choice IDs without exposing native transport handles beyond the already
opaque public request reference.

A `secret` question is not evidence that plain IM is secure. The portable
presenter emits only a bounded unsupported notice and returns
`response_supported=False`, so Gateway creates no response correlation. A
Channel-native presenter may support secret input only through a separately
evidenced secure Channel capability; Core does not define a universal card
format.

## Bounds and failure

Input request/question/choice counts and field lengths are bounded by the
typed Application contract: at most 32 questions per request and 64 choices
per approval or question. The presentation has finite content/item/text limits
and fails explicitly if those limits or fixed identities are violated.
One destination's rendering or delivery failure does not authorize a response
or block another destination.

The presenter is stateless and replay-safe for the same request/destination
facts. Rendering does not resolve a request. A crash after native Channel
delivery but before durable correlation may leave an unanswerable prompt when
the Application offers no authoritative request replay; the presenter must
not invent request state to close that gap.

## Physical migration

`src/imagent/interaction/controllers/request_presentation.py` owns the exact
presentation protocol and portable Markdown implementation. The focused
mechanical extraction moved those objects without changing rendering, bounds,
correlation, delivery, replay, or security behavior. During the finite
Controller package migration,
`imagent.controllers` remains an exact-object public facade; the historical
mixed modules retain no second implementation.

This leaf remains separate from command registration and common-command
parsing. The move does not introduce a registry, product command, Channel card
protocol, or request mutation.

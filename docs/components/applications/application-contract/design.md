# Application contract design

Component ID: `applications.application-contract`

Parent: `applications`

## Purpose and ownership

This leaf defines the common typed boundary through which Gateway uses one
native Agent Application without claiming its truth. Consumer Controllers use
typed `ControllerActions`; they do not depend directly on an adapter.
It owns `AgentApplicationAdapter`, `ApplicationSummary`, typed input dispatch
facts/results, the complete Project/Thread/Turn/resource/history model family,
`AgentMessage`, `ApplicationInputOutcomeUnknown`, `validate_thread_ref`, and
the classification of a native input outcome as known or unknown.

It does not own Gateway admission, Conversation binding, projection routes,
checkpoints, request-destination correlation, product commands, a native wire
implementation, or an Agent transcript/runtime. The concrete Application owns
Projects, Threads, Turns, requests, execution, and authoritative history.

## Inputs, outputs, and public boundary

The adapter accepts typed Application Operations and `AgentInput`; it returns
typed operation results, an `AcceptedTurn`, and independent Thread event
subscriptions. `send_input()` defaults to `prefer_active_turn`, invokes the
typed pre-dispatch callback exactly once immediately before a native mutation,
and reports the actual `started/create_new` or `steered/preserve_existing`
result. `ApplicationInputOutcomeUnknown` is the exact `RuntimeError` raised
when a native input may have mutated after dispatch; its constructor remains
`(message: str, cause: BaseException)` and stores the original cause on
`.cause`. `AgentMessage` is the immutable, strong-`ThreadRef`-scoped item
shared by authoritative history and canonical live-event normalization.

The owner contracts/exports are the complete family listed below, plus
`AgentApplicationAdapter` and `ApplicationInputDispatchHandler`, from
`src/imagent/applications/contract.py`:

`Page`, `ApplicationRef`, `ProjectRef`, `ThreadRef`,
`InputContinuationPreference`, `InputDisposition`,
`TurnReplyCorrelationPolicy`, `ThreadStatus`, `ApplicationSummary`,
`ProjectSummary`, `ThreadSummary`, `AgentInput`, `AgentMessage`, `TurnStatus`,
`TurnCatchup`, `TurnHistoryEntry`, `ThreadHistory`, `ThreadSnapshot`,
`AcceptedTurn`, `ApplicationInputDispatch`, `ApplicationInputOutcomeUnknown`,
and `validate_thread_ref`.

The finite `imagent.applications` facade exposes those exact objects. The
deliberate `imagent.contracts` facade retains the exact compatibility alias for
`ApplicationInputOutcomeUnknown`, but it contains no second implementation and
does not expose the other Application contract family, capability, operation,
or request names. Capabilities, operations, and requests are owner-only
surfaces in their respective Applications modules. The root facade does not
eagerly import concrete adapters; explicit named concrete exports remain a
finite facade behavior.
The historical `imagent.contracts.errors` module is retired after this move
and is not an internal compatibility path. The historical `imagent.adapters`
surface no longer exports the Application adapter Protocol or callback. It
retains only unrelated Gateway aliases owned elsewhere; its historical
Channel/admission names were retired when the Interaction Channel facade
became authoritative.

## Dependency, state, and recovery boundary

This leaf consumes Interaction `Content`, `MessageRole`, and `Metadata` values
and the Applications capability, event, operation, and request leaves. It does
not depend on Gateway implementation. The events leaf consumes the owned
`AgentMessage`; that dependency does not transfer item, history, or Thread
authority to events. Native adapters declare only the replay/order facts
that their Application actually supports. After a native input may have been
dispatched, timeout, cancellation, disconnect, or lost response is the
canonical `ApplicationInputOutcomeUnknown` unless native truth proves
otherwise; it never reopens Gateway retry permission. Known pre-dispatch
rejections remain their original exception types and do not use the unknown
outcome wrapper.

Managed, flat, and fixed Project shapes are explicit through the capability
leaf's `ProjectMode`. Binding a returned `ThreadSummary` is a separate Gateway
mutation and cannot activate a native Thread. An adapter must not silently
invent steer/queue behavior when a native Application supports only a started
Turn.

## Current and target structure

The owner implementation is `src/imagent/applications/contract.py` with
focused ownership evidence in `tests/applications/test_contract.py`. Shared
JSON Schemas remain cross-owner language-neutral documents; their Python
reference values are not duplicated in the retired contracts model module or
Interaction. `imagent.contracts` re-exports the exact owner object only for
`ApplicationInputOutcomeUnknown`, while `imagent.applications` directly
exposes the complete contract family. Its finite named lazy facade is limited
to explicit concrete adapter/presentation exports and cannot hide ownership or
solve an import cycle. Capabilities, operations, and requests are not
package-root exports. `src/imagent/adapters.py` retains only unrelated Gateway
aliases; the Application Protocol and callback are no longer available there.
The conformance suite remains affected evidence for all concrete adapters.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Ports design](../../ports/design.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
- [ADR 0012](../../../decisions/0012-input-continuation-and-reply-correlation.md)

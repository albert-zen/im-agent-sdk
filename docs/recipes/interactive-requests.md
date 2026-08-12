# Recipe: present and answer an interactive request

Project typed Application requests through the configured presenter, then
answer only through the Conversation that received the request. Knowledge of a
request ID alone is not response authority.

## Prerequisites

- An Application adapter that advertises and implements the relevant request
  kind and, for restart recovery, an authoritative pending-request snapshot.
- A configured `RequestPresenter` such as `MarkdownRequestPresenter`.
- An accepted request delivery to the responding Conversation.
- Consumer-owned sender admission and approval/permission policy.
- A stable action ID derived from the native UI action or command identity.

## Public API path

```python
from imagent import Failed, OutcomeUnknown, Partial, Succeeded
from imagent.applications.requests import ApprovalResponse

async with gateway:
    actions = gateway.actions(conversation_ref, actor=authenticated_actor)
    result = await actions.respond_request(
        request_ref,
        ApprovalResponse(choice_id="approve-once"),
        action_id="request-card:interaction-772",
    )

    if isinstance(result, Succeeded):
        record_resolution(result.value.ref)
    elif isinstance(result, (Failed, Partial, OutcomeUnknown)):
        present_typed_failure(result.error.code.value)
```

For structured input, pass
`UserInputResponse(answers={question_id: (answer,)})`. The response is bounded
and validated against the shape delivered to this Conversation before the
native side-effect fence. Do not call a concrete adapter or invent separate
button/card semantics.

## Owner and authority

Applications own request and first-writer resolution truth. Gateway owns only
minimal per-destination response correlation after accepted delivery.
Interaction owns typed, bounded presentation. Product authorization, secure
secret collection, Full Access, sandbox, and wording remain outside SDK Core.
See [Application request design](../components/applications/requests/design.md),
[request correlation](../components/gateway/projection/request-correlation/design.md),
and [ADR 0008](../decisions/0008-interactive-request-routing.md).

## Typed failure modes

- `unauthorized_destination`: this Conversation did not receive an accepted
  presentation.
- `request_duplicate`: another response already won.
- `request_resolved`: native terminal truth already resolved the request.
- `request_stale`: transport/restart evidence cannot prove the request remains
  answerable.
- `unsupported`: the adapter or presenter does not support this request shape;
  portable secret questions intentionally create no response correlation.
- `conflict`: the action ID was reused with a different response.
- `native_outcome_unknown`/`OutcomeUnknown`: the response may have reached the
  Application; never submit it again under a new ID.

## Diagnostics

Use `gateway.diagnostics()` for fixed projection/recovery and adapter facts.
No request ID, prompt, answer, or provider error appears there. Confirm pending
truth through the native Application; after a gap, absence of an authoritative
pending snapshot is a typed degradation, not permission to reconstruct a
request from Gateway correlation.

## Executable evidence

- `PYTHONPATH=src python -m unittest tests.applications.test_requests -v`
- `PYTHONPATH=src python -m unittest tests.gateway.projection.test_request_correlation -v`
- `PYTHONPATH=src python -m unittest tests.interaction.controllers.test_request_presentation -v`
- `PYTHONPATH=src python -m unittest tests.applications.adapters.appserver.test_gateway_request_integration -v`

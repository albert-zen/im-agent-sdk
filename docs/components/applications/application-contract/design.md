# Application contract design

Component ID: `applications.application-contract`

Parent: `applications`

## Purpose and ownership

This leaf defines the common typed boundary through which Gateway uses one
native Agent Application without claiming its truth. Consumer Controllers use
typed `ControllerActions`; they do not depend directly on an adapter.
It owns `AgentApplicationAdapter`, `ApplicationSummary`, typed input dispatch
facts/results, resource/history reference values, and the classification of a
native input outcome as known or unknown.

It does not own Gateway admission, Conversation binding, projection routes,
checkpoints, request-destination correlation, product commands, a native wire
implementation, or an Agent transcript/runtime. The concrete Application owns
Projects, Threads, Turns, requests, execution, and authoritative history.

## Inputs, outputs, and public boundary

The adapter accepts typed Application Operations and `AgentInput`; it returns
typed operation results, an `AcceptedTurn`, and independent Thread event
subscriptions. `send_input()` defaults to `prefer_active_turn`, invokes the
typed pre-dispatch callback exactly once immediately before a native mutation, and
reports the actual `started/create_new` or `steered/preserve_existing` result.

The present public contracts/exports are `AgentApplicationAdapter` from
`imagent.adapters` and the `ApplicationSummary`, resource, history, input,
and validation values from `imagent.contracts`. The target formal facade is
`imagent.applications`, backed by `src/imagent/applications/contract.py`; it
must re-export exact objects rather than retain a second implementation.

## Dependency, state, and recovery boundary

This leaf consumes Interaction message/operation values and the Applications
capability, event, operation, and request leaves. It does not depend on
Gateway implementation. Native adapters declare only the replay/order facts
that their Application actually supports. After a native input may have been
dispatched, timeout, cancellation, disconnect, or lost response is
`ApplicationInputOutcomeUnknown` unless native truth proves otherwise; it
never reopens Gateway retry permission.

Managed, flat, and fixed Project shapes are explicit. Binding a returned
`ThreadSummary` is a separate Gateway mutation and cannot activate a native
Thread. An adapter must not silently invent steer/queue behavior when a native
Application supports only a started Turn.

## Current and target structure

Current code is split among `src/imagent/adapters.py`,
`src/imagent/contracts/{_validation.py,errors.py,model.py,validators.py}`,
`src/imagent/applications/__init__.py`, `src/imagent/diagnostics.py`, and the
v1 common/history/messages/resources schemas. Current conformance evidence is
`tests/test_adapter_contracts.py`.

The target is `src/imagent/applications/contract.py` with
`tests/applications/test_contract.py`; shared JSON Schemas remain
language-neutral documents. The current common Application Protocol sharing
`adapters.py` with Channel and repository Ports is the declared structural
gap. No compatibility implementation or semantics is added by this
documentation slice.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Ports design](../../ports/design.md)
- [ADR 0001](../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0002](../../../decisions/0002-design-authority-and-control-boundaries.md)
- [ADR 0012](../../../decisions/0012-input-continuation-and-reply-correlation.md)

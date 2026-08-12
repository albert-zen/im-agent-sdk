# Recipe: deliver proactively through existing routes

Use the canonical Gateway delivery path with an opaque scoped credential and a
stable delivery ID. The first reservation pins destinations, so later replay
cannot follow a moved route.

## Prerequisites

- A running Gateway configured with a `DeliveryAuthorizer` and the normal
  Channel delivery coordinator.
- A consumer-issued credential whose `DeliveryPrincipal` scope covers the
  exact Thread or Conversation target.
- A caller-stable delivery ID derived from the upstream task/action identity.
- Complete content capability preflight and consumer-owned artifact lifetime.
- An explicit consumer decision about retry appetite; unknown is never safe to
  resend.

## Public API path

```python
from datetime import UTC, datetime

from imagent.gateway.delivery import DeliveryIntent, ThreadRouteDeliveryTarget
from imagent.interaction.messages import TextContent

target = ThreadRouteDeliveryTarget(thread_ref=thread_ref)

# Authorize before caller-owned artifact acquisition or decoding.
await gateway.authorize_proactive_target(target, credential=credential)

intent = DeliveryIntent(
    delivery_id="agent-task:run-018:publish",
    target=target,
    content=(TextContent("The run completed."),),
    created_at=datetime.now(UTC),
)
result = await gateway.deliver_proactively(intent, credential=credential)

if result.state.value == "unknown":
    stop_automatic_retry(result)
elif result.state.value == "retryable":
    schedule_explicit_same_id_retry(result)
```

For a specific destination use `ConversationDeliveryTarget`. For an external
loopback tool, mount `ProactiveDeliveryJsonHandler` in a consumer-authenticated
local service or use `imagent-send`; neither is a job queue or server framework.
Never read Gateway rows or expose bot credentials/native Conversation IDs to
an Agent task.

## Owner and authority

Gateway owns authorization ordering, route resolution, immutable submission
snapshots, and typed outcomes. Channels own native delivery truth. Consumers
own credential issuance/storage/rotation, local HTTP mounting, artifact bytes,
and retry policy. See [proactive delivery](../components/gateway/delivery/proactive-delivery/design.md),
[authorization](../components/gateway/delivery/proactive-authorization/design.md),
and [ADR 0009](../decisions/0009-proactive-delivery-routing.md).

## Typed failure modes

- `DeliveryAuthorizationError`: missing, invalid, revoked, or out-of-scope
  credential; no routing or Channel work is authorized.
- `DeliveryPlanningError`: the complete content cannot be represented by the
  Channel capabilities; no Channel side effect occurs.
- `DeliverySubmissionCapacityError` / `delivery_capacity_exhausted`: durable
  submission admission failed before Channel work.
- `delivery_ingress_capacity_exhausted`: the process-local JSON delivery-ID
  registry rejected a distinct active ID before authorization or staging.
- `conflict`: one delivery ID was reused with changed target, payload, or
  first-reservation destination snapshots.
- `rejected` or `partial`: inspect per-destination typed states/receipts.
- `retryable`: only this persisted explicit receipt permits same-ID resumption.
- `unknown`: a Channel side effect may have occurred; it remains sticky.

## Diagnostics

Use `gateway.diagnostics()` for bounded delivery/Channel facts and O2 failure
counters. Proactive results contain sanitized route and receipt facts;
Thread-targeted results omit native Conversation identity. Credentials,
payloads, paths, and free-form native errors never belong in SDK diagnostics.

## Executable evidence

- `PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_authorization -v`
- `PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_delivery -v`
- `PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_ingress -v`
- `PYTHONPATH=src:. python -m unittest tests.gateway.test_reference_consumer -v`

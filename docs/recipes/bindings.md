# Recipe: bind a Conversation to a Thread

Use the scoped action surface to select where the next input goes. Binding,
native UI activation, and output observation are separate intents; under
`foreground_only`, a successful Thread bind also establishes the matching
output route atomically.

## Prerequisites

- A running `Gateway` with the target Application registered.
- An authenticated `ConversationRef` and stable actor identity admitted by the
  Channel or consumer.
- An authoritative `ThreadRef` whose `ProjectRef` ancestry is intact.
- A caller-stable `action_id`. Reuse it only for the identical semantic action.
- Consumer-owned authorization for the actor and chosen resource.

## Public API path

```python
from imagent import Failed, OutcomeUnknown, Partial, Succeeded

async with gateway:
    actions = gateway.actions(conversation_ref, actor=authenticated_actor)
    current = await actions.get_binding()
    result = await actions.bind_thread(
        thread_ref,
        action_id="ui:bind:message-018:thread-42",
        expected_generation=None if current is None else current.generation,
    )

    if isinstance(result, Succeeded):
        generation = result.value.binding_generation
    elif isinstance(result, Partial):
        # Durable binding succeeded, but process-local route activation did not.
        schedule_same_id_reconciliation(result.error.code.value)
    elif isinstance(result, OutcomeUnknown):
        stop_automatic_retry(result.error.code.value)
    elif isinstance(result, Failed):
        present_typed_failure(result.error.code.value)
```

Use `select_application`, `select_project`, `clear_thread`, `clear_project`, or
`clear_application` for the other hierarchical transitions. Use
`observe_thread` only for output observation, and `activate_native_thread` only
for native Application UI state. Do not reproduce `create_and_bind_thread` by
sequencing primitive create and bind calls.

## Owner and authority

Gateway owns the Conversation binding and its monotonic generation. The Agent
Application owns Project and Thread existence. Product defaults, permissions,
confirmation, command grammar, and actor admission remain consumer policy.
See [scoped actions](../components/gateway/actions/design.md),
[binding design](../components/gateway/routing/bindings/design.md), and
[ADR 0016](../decisions/0016-uniform-workspace-and-consumer-actions.md).

## Typed failure modes

- `unsupported`: the Application does not advertise the needed capability.
- `stale_binding`: the expected generation or resource ancestry is stale.
- `conflict`: the action ID was reused with different intent.
- `capacity_exhausted`: the bounded receipt or projection capacity rejected
  the action before the relevant side effect.
- `stale_runtime`: shutdown or lease loss won the lifecycle fence.
- `Partial`: the durable binding may be known while route activation failed.
- `OutcomeUnknown`: native work may have happened; never blindly repeat it.

Ordinary input without a complete binding separately fails with the typed
`missing_binding` operation code. Never choose a sole Application or default
CWD in SDK Core to hide that result.

## Diagnostics

Read `gateway.diagnostics()` while the Gateway is running. Projection degraded
counts and fixed gap codes can show activation or recovery pressure, but the
snapshot intentionally omits Conversation, route, Thread, and error text.
Confirm the exact binding with `await actions.get_binding()` and confirm
resource truth through `get_project`/`get_thread`.

## Executable evidence

- `PYTHONPATH=src python -m unittest tests.gateway.test_actions -v`
- `PYTHONPATH=src python -m unittest tests.gateway.routing.test_bindings -v`
- `PYTHONPATH=src python -m unittest tests.gateway.test_reference_consumer -v`

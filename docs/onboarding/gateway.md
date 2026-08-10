# Gateway: composition and routing

Gateway is the single composition root. Construct it with explicit
Applications, Channels, one coherent store, limits, and an optional frozen
Controller:

```python
gateway = Gateway(
    gateway_id="reference",
    channels=[channel],
    applications=[application],
    store=MemoryGatewayStore(),
    limits=GatewayLimits(...),
    controller=registry,
    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
)
```

The complete neutral wiring is in
[`build_reference_consumer()`](../../examples/reference_consumer/gateway.py).
The store is an explicit composition value. It contains only IM bridge state,
idempotency/effect receipts, and rebuildable projections; it contains no
transcript or Application execution truth. Gateway alone acquires its coherent
store session and wires the private fenced executor used by public actions.
The consumer never imports or constructs that seam.

## Scoped consumer actions

Acquire actions only while the owned Gateway context is running:

```python
async with gateway:
    actions = gateway.actions(conversation.ref, actor=conversation.authenticated_actor)
    project_result = await actions.create_and_select_project(
        application.ref,
        cwd=managed_cwd,
        action_id="reference:create-project:1",
    )
    if not isinstance(project_result, Succeeded):
        raise RuntimeError("reference Project workflow did not succeed")
    thread_result = await actions.create_and_bind_thread(
        project_result.value.ref,
        action_id="reference:create-thread:1",
    )
    if not isinstance(thread_result, Succeeded):
        raise RuntimeError("reference Thread workflow did not succeed")
```

Handle the typed `Succeeded`, `Failed`, `Partial`, and `OutcomeUnknown`
variants explicitly. Reuse stable action IDs only for identical intent. The
Conversation scope cannot be substituted by a handler, and the Application
surface never gains Conversation binding authority.

## Binding and observation are different

`ConversationBinding` selects the Application Thread for the next input.
`ThreadProjectionRoute` selects where Thread output may be delivered. Native
Thread activation is a separate Application operation. Use
Conversation-scoped actions for each intent:

- `bind_thread` changes future input selection;
- `observe_thread` explicitly adds or refreshes an output route for policies
  that retain unbound observers;
- `ProjectionPolicy.FOREGROUND_ONLY` makes binding equality the output
  authority and prepares the matching route before the binding commit.

Under `foreground_only`, multiple Conversations may bind the same Thread. The
Gateway creates one Application observation worker for that Thread and fans
the canonical events to each active route. If Conversation A switches to a
different Thread, its old route loses authority immediately; Conversation B’s
route to the original Thread remains active.

The sample and its test exercise this exact sequence with two bindings. They do
not inspect native Conversation IDs or create a consumer-side subscription.

## Lifecycle and recovery

Start and stop the composed Gateway as one owner:

```python
async with gateway:
    # bind and accept input through typed contracts
    ...
```

Startup validates composition, acquires the store lease, starts the Application
and Channel, and releases projection delivery. Stop closes observation and
bounded delivery work, the Controller, adapters, and store lease/resources.
The reference run constructs a fresh Gateway once and proves that no owned task
or subscription remains. Durable fresh-object restart is a separate SQLite
acceptance scenario.

Read `diagnostics()` synchronously for redacted, process-local
facts. It is not authoritative health state and does not perform I/O.

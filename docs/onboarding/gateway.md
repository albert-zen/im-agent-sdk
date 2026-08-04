# Gateway: composition and routing

Gateway is the single composition root. Construct it with explicit
Applications, Channels, bridge repositories, limits, and typed extensions:

```python
gateway = ImAgentGateway(
    channels=[channel],
    applications=[application],
    repositories=GatewayRepositories(
        bindings=bindings,
        projections=projections,
    ),
    limits=GatewayLimits(...),
    extensions=GatewayExtensions(controller=registry),
    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
)
```

The complete neutral wiring is in
[`build_reference_consumer()`](../../examples/reference_consumer/gateway.py).
The repositories are explicit composition values. They are not looked up by
name at runtime and they do not contain transcript or execution state.

## Binding and observation are different

`ConversationBinding` selects the Application Thread for the next input.
`ThreadProjectionRoute` selects where Thread output may be delivered. Native
Thread activation is a separate Application operation. Use typed Gateway
operations for each intent:

- `BindConversationToThread` changes future input selection;
- `ObserveThread` explicitly adds or refreshes an output route for policies
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
await gateway.start()
try:
    # bind and accept input through typed contracts
    ...
finally:
    await gateway.stop()
```

Startup restores persisted routes, starts the Application and Channel, then
releases projection delivery. Stop closes observation and bounded delivery
work before stopping the lower adapters. The sample reuses the same
process-local Application object and bridge repository objects across a
Gateway stop/start, so its bounded Application history remains available for
the demonstration. On restart, the Gateway subscribes first and reconciles
bounded history/catch-up, so output produced while it was stopped can be
delivered to the still-active route without an SDK transcript. Production
deployments require durable Application history plus durable Gateway
repositories.

Read `diagnostics_snapshot()` synchronously for redacted, process-local
facts. It is not authoritative health state and does not perform I/O.

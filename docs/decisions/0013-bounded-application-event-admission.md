# ADR 0013: Bounded Application event admission and recovery

Status: Accepted

## Context

ADR 0004 gave every live Thread observer an independent queue so a slow
projection could not block an Application notification callback or steal from
another observer. Those queues were unbounded. Gateway startup callbacks and
the short event buffer used while `AcceptedTurn` correlation is being recorded
were also process-local, unbounded accumulations. A stalled destination could
therefore retain native Application events without limit even though Channel
delivery admission was bounded by ADR 0010.

The SDK cannot solve this by persisting an event journal. Completed messages
and Turn truth belong to the Agent Application, and some interactive requests
cannot be reconstructed after their transient open event is lost.

## Decision

### Every bridge-owned admission point has a finite capacity

Each live `EventBroadcaster` subscription has an independently configurable
positive event limit. Gateway startup uses one FIFO capacity shared by inbound
messages and typed operations. The per-Thread buffer that holds events until
all concurrent input acceptances have recorded reply correlation also has a
positive configurable limit.

Application adapters, Gateway startup, and projection runtime provide safe
defaults. Consumers may tune capacity, but cannot select silent loss.
App Server notification and server-request dispatch retain their existing
separate bounded queues. Interactive request presentation retains its existing
bounded managed-task admission; it does not share completed-message recovery
assumptions.

### App Server exposes an ordered admission fence, not a Core event sequence

The App Server reader keeps JSON-RPC responses on its socket fast path. Before
completing a response, every earlier notification or server request has either
been admitted to its bounded callback lane or has failed the connection with
explicit overflow. Each admitted callback carries a public
`AppServerDispatchPosition(connection_epoch, sequence)`. Calls that need an
ordering gate use `call_with_dispatch_position` and receive an immutable
`AppServerResponse` containing the exact position observed at that response,
so frames read after the response or after reconnect cannot widen its fence.
Positions are monotonic only within one connection epoch and reset to zero on
reconnect. The mutable last-admitted position is diagnostic/compatibility
state, not a response fence.

Notification and server-request handlers may complete out of order because
their lanes are intentionally isolated. A consumer that must hold live native
output behind its own immediate response can gate callback admission by the
public position and release through the response-side fence. Positions are
transient adapter handoff metadata: they are not `AgentEvent.sequence`, a
replay cursor, durable state, or permission to reconstruct native history.
The SDK Gateway does not persist or interpret the fence; its Turn-acceptance
buffer and authoritative recovery already own the corresponding common
projection race.

### Overflow terminates only the affected live observation

Publishing remains synchronous and non-blocking with respect to Channel
delivery. When one subscriber fills, the broadcaster removes that subscriber,
discards its bounded queued projection, and makes its next read raise a typed
overflow error. Other subscribers and Threads continue normally.

An App Server callback-lane overflow resets its connection. That reset
terminates every current App Server Application subscription with an explicit
connection-scoped event gap, so Gateway cannot remain falsely healthy after
completed notifications or not-yet-mapped server requests were lost. Any
unexpected projection-worker restart likewise triggers pending-request
snapshot reconciliation when native support exists, or truthful degraded
request health when it does not.

A per-Thread acceptance-buffer overflow raises the same class of explicit
infrastructure gap. Input already accepted by the Application remains beyond
the side-effect boundary; Gateway never releases its inbound idempotency claim
to manufacture a second Turn. Startup admission overflow fails startup
explicitly and runs normal component teardown instead of dropping an inbound
mutation.
Once startup fails or shutdown begins, Channel callbacks are explicitly
rejected until a later successful start; they cannot bypass the startup buffer
and reach a stopping Application.

### Recovery remains native-authoritative

Projection supervision records a bounded, non-secret overflow count and gap
code, resubscribes only the affected Thread with bounded backoff, and performs
the existing bounded authoritative history/catch-up reconciliation. Native
replay may be used when an adapter truthfully advertises it. The SDK never
stores event bodies to bridge the gap.

When an Application exposes an authoritative pending-request snapshot, event
gap recovery also reconciles that snapshot for only the affected Thread
through the existing bounded request presenter. Without such a snapshot,
health remains explicitly marked
as interactive-request recovery degraded. Existing delivered correlations are
not guessed stale merely because an event gap occurred, and missing request
prompts are not manufactured from bridge state.

## Classification

- **Core invariant:** finite bridge-owned admission, non-blocking publication,
  explicit overflow, per-Thread failure isolation, and no synthetic event log.
- **Optional capability:** native replay and authoritative pending-request
  snapshot recovery.
- **Adapter-specific policy:** native transport queue/reset behavior and the
  configured Application subscriber capacity; App Server epoch-scoped ordered
  callback admission.
- **Consumer policy:** concrete capacity values and product retry/degraded UX.

## Cross-product evidence

Codex/Zen App Server notifications and T3 polling both publish through the
same live fan-out primitive and recover completed output from authoritative
Thread/Turn reads. App Server transport additionally proves bounded
notification/server-request lanes; T3 proves that a polling producer must stop
or continue independently when one observer overflows. Codex request mapping
provides the counterexample: without a native pending snapshot, a lost open
request is not recoverable like a completed message.

## Consequences

Memory retained by each admission point is bounded by configuration and the
number of active bridge resources. A slow observer may see a gap and duplicate
authoritative completed items during reconciliation, but stable delivery IDs
and checkpoints converge those duplicates. Request recovery can remain
degraded until native truth supplies a new event or snapshot; this is safer
than pretending that message history contains pending request state.

# ADR 0004: Event fan-out and authoritative recovery

Status: Accepted

## Context

A shared queue let observers steal events, slow IM delivery could block native
notification paths, and synthetic counters could look replayable despite
resetting on restart.

## Decision

### Live Thread subscriptions fan out

Every active subscriber has an independent queue and consumption position.
Native producers publish without awaiting consumers. The queues are live
projections, not transcript or execution authority.

`message.completed` does not terminate a Turn. Only explicit
`turn.completed`, `turn.failed`, or `turn.interrupted` does.

### Ordering fields state native guarantees

`eventId` is required. `sequence` plus epoch and `cursor` are optional and
appear only when their scope/replay meaning survives the declared window.
Cursor expiration and detected gaps are explicit.

Without native replay, recovery establishes live observation and reconciles
stable IDs from authoritative history/catch-up. The SDK does not create a
synthetic event journal.

## Consequences

Multiple IM/client observers see the same canonical events. Recovery remains
native-authoritative. [ADR 0012](0012-bounded-application-event-admission.md)
bounds each independent live queue, terminates only an overflowed subscriber,
and enters explicit authoritative gap recovery. Delivery execution policy
remains separately bounded by ADR 0010.

# Gateway projection components

Gateway projection turns normalized Application observation and authoritative
history into rebuildable per-Conversation output decisions. It composes one
Application observation worker for each active Thread and fans its canonical
output to the active routes. It does not own the native subscription producer,
the Agent transcript or request truth, a second event journal, a content spool,
or Channel delivery policy.

## Leaves

- [observation](observation/design.md) and [testing](observation/testing.md) —
  one bounded active Thread worker and independent live fan-out.
- [checkpoints](checkpoints/design.md) and [testing](checkpoints/testing.md) —
  per-route durable completion boundaries, expected-current checkpoint CAS,
  and idempotent convergence from completed delivery evidence.
- [request correlation](request-correlation/design.md) and
  [testing](request-correlation/testing.md) — minimal Turn reply and
  interactive-request destination authority.
- [recovery](recovery/design.md) and [testing](recovery/testing.md) — bounded
  authoritative reconciliation, gap classification, and resubscription inputs.

Routing owns durable destination-edge policy; projection consumes those edges.
Delivery owns planning and Channel execution; projection only supplies the
ordered destination decision. Persistence owns passive records and atomic
repository implementation. The [component map](../../component-map.yml)
records the canonical recovery owner, the remaining mixed observation/input
runtime, and the historical route-delivery coordination gap.

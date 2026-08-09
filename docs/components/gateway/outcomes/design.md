# Gateway outcome algebra design

Component ID: `gateway.outcomes`

Parent: `gateway`

## Purpose

This leaf owns the language-neutral closed runtime result algebra used by v1
actions: `Succeeded(value)`, `Failed(error)`, `Partial(value, error)`, and
`OutcomeUnknown(error)`. The four discriminants are stable protocol values;
expected runtime failures are returned rather than hidden in Boolean results
or unbounded exception payloads.

The algebra is generic and immutable. It does not define product errors,
retry policy, adapter behavior, persistence phases, orchestration, or a
compatibility result wrapper. Gateway effect execution supplies the bounded
`ActionError` and `EffectValue` projections appropriate to durable receipts.

## Authority

- [V1 design](../../../V1_DESIGN.md)
- [V1 executable specification](../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

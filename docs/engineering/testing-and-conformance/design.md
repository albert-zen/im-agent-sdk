# Testing and conformance design

## Purpose

Testing and conformance provides reusable evidence that a Channel or Agent
Application adapter implements the shared contracts honestly. It makes it
possible to add an integration without moving product behavior into Core.

## Ownership

This leaf owns:

- bounded fake Channel and Agent Application adapters used to exercise the
  contract kit;
- reusable Channel and Application contract checks;
- representative capability and resource fixtures;
- cross-integration assertions for stable identity, lifecycle, ordering,
  recovery, and explicit unsupported behavior;
- conformance checks for an admitted typed extension position, including its
  identity, bounded values, absent-extension behavior, and authority boundary.

It does not own production runtime behavior, native clients, product test
policy, deployment fixtures, or an adapter's native resource authority.

## Dependencies

The kit depends on public Contracts, Ports, and event fan-out primitives.
Product code never depends on the test kit. Fakes model contract behavior only
and must not exceed the semantics of real adapters.

## Conformance boundary

The JSON Schemas are the language-neutral contract surface. The Python kit is a
reference implementation and a set of checks, not a replacement wire
protocol. A fake models only the public contract under test; it must not become
a richer second runtime, transcript, subscription, admission path, or delivery
system.

A common semantic needs evidence from at least two real implementations on the
relevant side, or a concrete counterexample that explains why the invariant is
still required. A capability declaration or explicit unsupported result is
valid conformance. Silent approximation is not. Stable IDs, not text or time,
drive identity and idempotency assertions.

## Typed extension evidence

ADR 0015 keeps extension positions separate. Tests must name the position and
its effect contract rather than accepting a generic callback fixture. In
particular, extension coverage proves:

- the input and output are immutable, typed, bounded, and free of raw native
  envelopes or implicit filesystem authority;
- work is admitted on a bounded lane and does not run on a socket read path;
- the stable stage identity and replay rule are the documented ones;
- the extension cannot mutate bindings, routes, checkpoints, request/Turn
  correlation, credentials, or native retry decisions; and
- omitting the extension preserves the existing path.

The stage-specific suites distinguish safe pre-dispatch re-entry,
authoritative live/history recovery, durable per-destination suppression, and
best-effort non-durable observation. One generic middleware harness is not a
conformance abstraction.

Application input checks also assert that the typed pre-dispatch position is
invoked when applicable and that the adapter truthfully reports the accepted
`started` or `steered` correlation policy. The kit never chooses a native
continuation strategy on an adapter's behalf.

## Change authority

Changes to the kit or its fixtures first identify the shared contract and the
real integrations that prove it. The affected runtime leaf remains the owner
of semantic details; this leaf records only reusable evidence and test
mechanics. New fixture state must have a finite bound and a stated recovery
meaning. If a proposed assertion would require product permissions, retry
appetite, native rendering, or Application execution policy, it belongs in the
adapter or consumer tests instead.

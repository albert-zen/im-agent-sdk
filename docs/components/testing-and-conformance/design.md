# Testing and conformance component design

## Purpose

The reusable test kit checks whether adapters tell the truth about common
contracts. It lets new integrations prove compatibility without moving product
behavior into Core.

## Ownership

This component owns:

- fake Channel and Agent Application adapters;
- reusable Channel/Application contract verification;
- representative capabilities and fixture resources;
- cross-adapter assertions that stable IDs, lifecycle, and unsupported
  behavior remain consistent.
- common input assertions that every Application adapter invokes the typed
  pre-dispatch hook and truthfully returns started/steered correlation policy.
- reusable absent-extension counterexamples and stage-specific assertions for
  ADR 0015 Ports once each seam is admitted.

It does not own:

- production runtime behavior;
- native API clients;
- product test policy or deployment fixtures;
- authority over an adapter's native resources.

## Dependencies

The test kit depends on Contracts, Ports, and event fan-out primitives. Product
code never depends on the test kit.

Fakes model contract behavior only. They must not become a richer second
runtime whose semantics exceed real adapters.

## Conformance threshold

A proposed common semantic is accepted only after the contract test can be
passed honestly by at least two real integrations on the relevant side.
Capability declarations and explicit unsupported outcomes are valid
conformance; silent approximation is not.

Extension conformance never provides a generic fake middleware runtime. It
checks the exact typed position, stable identity, bounded values, default
absence, and that the extension receives no Gateway/repository authority.

## Change obligations

Changes to `src/imagent/testing/` require checking all concrete adapters and
whether the fixture encodes accepted semantics from Contracts rather than a
single product assumption.

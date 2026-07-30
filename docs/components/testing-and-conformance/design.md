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

## Change obligations

Changes to `src/imagent/testing/` require checking all concrete adapters and
whether the fixture encodes accepted semantics from Contracts rather than a
single product assumption.

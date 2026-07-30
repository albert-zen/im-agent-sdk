# Roadmap and open decisions

This document contains future or unresolved work. Current behavior is defined
by Contracts, component docs, accepted ADRs, code, and tests.

## Projection maturity gate

Before reusable code is transferred in Issue #9, complete
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14) to harden the
existing projection runtime:

- regression-test the current non-yield, single-event-loop one-worker
  invariant and make lifecycle ownership explicit; add synchronization only
  if the execution model changes;
- establish a live baseline plus bounded recent/active catch-up on first
  observation instead of delivering a complete archive;
- use a Gateway-owned projection completion boundary for bounded restart
  reconciliation;
- separate long-lived destination reply context from per-Turn correlation;
- supervise failed projection workers and expose health/recovery behavior;
- record current unbounded subscriber queues until Issue #12 adds bounded
  delivery execution.

These are cross-Channel/Application projection concerns, not Codex-only
behavior. Native replay remains an optional Application capability and native
reply rendering remains Channel policy.

## Issue #9: transfer reusable ownership

Create a module-by-module transfer map, then remove the SDK's package/runtime/
dynamic-import dependency on IMCodex.

The SDK becomes owner of reusable Channel adapters, App Server client pieces,
tests, fixtures, and provenance. IMCodex becomes a downstream composition.
Do not keep permanent dual implementations or an exitless shim. IMCodex
configuration, product commands, bot policy, and permission choices remain
consumer decisions and are migrated later in a separate repository task.

## Issue #10: request response loop

Complete approval and user-input request observation/response while preserving
native Application request truth. The common surface may represent request
identity, capability, and typed responses. Full Access and automatic approval
are consumer/Application policy.

Open design evidence:

- compare Codex approval/user-input lifecycle with at least Zen and T3;
- define authoritative request recovery after reconnect;
- keep sandbox and IM admission separate.

## Issue #11: proactive content and Artifact send

Route proactive text/Artifact delivery through Gateway routes and idempotency.
Agent tools do not receive bot secrets or native Conversation IDs.

Open design evidence:

- prove the route/correlation model across at least two Channels;
- prove Artifact materialization across at least two Applications or keep
  native behavior in adapters;
- decide which parts are optional capabilities versus consumer policy.

## Issue #12: one delivery chain

Unify proactive delivery and event projection:

- a pure deterministic `DeliveryPlanner` maps authoritative content and
  destination capabilities to delivery steps;
- a `DeliveryCoordinator` performs ordered execution, bounded backpressure,
  idempotency, and explicit retry outcomes;
- native Channel adapters retain formatting, credentials, API limits, and
  platform retry semantics.

Do not introduce a durable job system until multiple real consumers prove that
the SDK, rather than a product/orchestrator, must own it.

## Issue #13: backlog

Issue #13 remains backlog and is not part of the current maturity sequence.

## Other open decisions

### Wire protocol

Embedded adapters use native language interfaces; remote Applications use
their native protocols through adapters. Add an SDK wire protocol only after
two out-of-process integrations prove the same transport requirement.

### Binding durability

The SDK ships repository Ports plus in-memory and durable implementations.
Choosing persistence is deployment policy, not an Agent runtime requirement.

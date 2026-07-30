# Roadmap and open decisions

This document contains future or unresolved work. Current behavior is defined
by Contracts, component docs, accepted ADRs, code, and tests.

## Issue #9: finish downstream consumer migration

The SDK owner-side transfer is represented by code, component docs, tests, and
the [transfer map](migrations/issue-9-imcodex-owner-transfer.md). The remaining
future work is an isolated IMCodex migration:

- depend on and compose the SDK-owned Channel/App Server APIs;
- retain product configuration, commands, branding, launchers, and policy;
- prove downstream parity, then delete the duplicated product implementations
  without an exitless shim.

Issue #9 remains open until that consumer-side removal is complete.

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

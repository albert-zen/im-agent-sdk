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

## Issue #10: downstream request-loop evidence

The SDK owner-side typed approval/user-input loop, destination-safe response
correlation, native pending-request reconciliation, and conformance coverage
are complete. IMZen supplies native Zen command-approval evidence over the
App Server adapter. Remaining evidence is consumer mounting against an
additional non-App-Server Agent Application. Full Access and automatic
approval remain consumer/Application policy.

## Issue #11: proactive content and Artifact send

The SDK owner-side implementation now provides scoped Gateway delivery,
immutable route snapshots, SQLite/in-memory outcomes, per-artifact receipts,
bounded inline staging, and an Interaction-owned loopback-only reference CLI.
Agent tools do not receive bot secrets, persistence access, or resolved native
Conversation IDs.

Remaining downstream evidence is to mount the handler in a real consumer and
exercise the one-command path against its configured Channels. Artifact upload
remains Channel-owned; no Application adapter needs an artificial Artifact
send API.

## Issue #12: downstream delivery-profile evidence

The SDK owner-side pure `DeliveryPlanner`, typed `DeliveryProfile`, shared
projection/proactive `DeliveryCoordinator`, destination ordering, bounded
admission, and conservative retry outcomes are complete. Remaining evidence
is downstream operation against real configured Channels and tuning profiles
without overstating native limits.

A durable job system remains out of scope until multiple real consumers prove
that the SDK, rather than a product/orchestrator, must own it.

## Issue #13: three-consumer v1 acceptance rewrite

After the v1 SDK candidate passes its own executable specification, create
isolated experimental branches/worktrees for IMCodex, IMT3, and IMZen and
rewrite their bridge composition against one exact SDK candidate commit or
wheel. This is a final acceptance layer, not an input to SDK architecture.

The experiments retain product policy downstream, remove duplicated bridge
authority, use only public SDK imports, and prove repository-native tests plus
real vertical message flows. A generic defect reopens the SDK candidate; a
consumer-specific need remains downstream unless the v1 extension evidence
rule is independently satisfied. Experimental branches require separate human
approval before merging to downstream defaults.

IMT3 is now a local Git repository, so its experimental branch/worktree can be
created without blocking final acceptance. A remote is not required for local
validation; publishing or merging that experiment remains a separate human
decision.

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

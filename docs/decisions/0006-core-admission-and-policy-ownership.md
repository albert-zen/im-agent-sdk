# ADR 0006: Core admission and policy ownership

Status: Accepted

## Context

The first consumer is IMCodex and the first mature Application seam includes
Codex. Designing only from that pair risks turning one product's commands,
permissions, retry choices, or runtime shape into universal SDK semantics.

## Decision

Every proposed common concept, state, Operation, recovery rule, or delivery
rule is tested against at least two different real implementations or a
concrete counterexample.

- Agent-side Core semantics normally require two Agent Applications.
- Channel-side Core behavior normally requires two IM Channels.
- One-product behavior stays in its adapter, consumer composition, optional
  Controller/contrib package, or a neutral extension point.

Designs classify behavior as:

1. **Core invariant** — required for safe interoperable meaning.
2. **Optional capability** — shared shape with honest implementation support.
3. **Adapter-specific policy** — native API, rendering, limit, or recovery
   behavior.
4. **Consumer policy** — product UX, permissions, configuration, retry
   appetite, or orchestration.

## Evidence examples

- stable event identity and one-worker observation are Core infrastructure
  across Codex/T3/Zen and QQ/Telegram/other Channels;
- native replay is an optional capability because Applications differ;
- text length units, attachment grouping, and destination FIFO are shared
  Channel-side contracts/infrastructure proven across QQ, Telegram, Feishu,
  and Weixin; native escaping and upload encoding remain adapter policy;
- IMCodex Full Access and command/config choices are consumer policy.

## Consequences

Reviewers may reject an abstraction even when it simplifies IMCodex. New Core
surface must cite cross-product evidence. SDK Issues #9–#12 cannot absorb
consumer policy merely because they migrate reusable code.

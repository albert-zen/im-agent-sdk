# ADR 0015: Application message presentation fidelity

- Status: Accepted
- Date: 2026-08-02

## Context

Application adapters already normalize native output into `AgentMessage` and
attach namespaced Metadata such as Codex message phase or T3 activity kind.
The projection boundary rebuilt `OutboundMessage` without that Metadata, so a
consumer could not apply its own visibility or presentation policy after the
Application adapter had removed the raw native payload.

The existing event vocabulary distinguishes a completed authoritative message
from a newly observed live message. T3 exposes recoverable activities beside
messages in authoritative Thread reads. Codex exposes both recoverable
completed items and live-only plan, diff, and status observations. Treating
all of them as completed projection checkpoints would make recovery search for
live-only identities that native history cannot reproduce. Exposing raw native
notifications to a consumer would instead create a second protocol path and
duplicate subscription runtime.

## Decision

Application adapters map presentation-capable native output to a normalized
`AgentMessage` before it leaves the adapter:

- authoritative items that can be returned by bounded history/catch-up use
  `message.completed` and their stable Application item identity;
- observations that are meaningful only while live use `message.created` and
  a stable native event identity;
- streaming fragments remain `message.delta` and are not delivered by the
  default completed-message projection.

Concrete adapters expose additional native activity/live presentation only
through an explicit option that defaults off unless that adapter already made
the item part of its authoritative history surface. Existing consumers do not
gain new visible tool/system output without choosing a presentation policy.

Gateway routes `message.created` through the same active-route lookup,
bootstrap barrier, per-route lock, idempotent delivery path, and common
Delivery Coordinator as completed messages. A created message never advances
the authoritative completion checkpoint and is never reconstructed from SDK
state. Its delivery identity is namespaced by the stable event ID so a later
`message.completed` for the same Agent item cannot be mistaken for the same
delivery. Overflow or reconnect may therefore omit a live-only presentation
while the worker records the existing explicit recovery gap.

Projection copies the adapter-normalized `AgentMessage.metadata` into the
`OutboundMessage`. Core does not interpret native phase, activity kind, or
visibility values. Metadata must remain bounded and must not contain raw
protocol envelopes, credentials, paths, content duplicates, or native resource
identities beyond the stable item/event identity already represented by the
typed message and event.

A consumer may decorate its Channel delivery boundary to suppress or render a
normalized message according to product visibility settings. Suppression is a
presentation decision over an already ingested event; it is not a second
Application subscription, transcript, or recovery authority.

The Codex/Zen adapter may also receive one optional `AppServerPresentationHook`.
Before publishing a completed item it supplies bounded typed item facts and
typed artifact candidates to that hook in the adapter's existing ordered
dispatch path. The same hook runs while bounded native history is normalized
and immediately before a live or authoritative Turn terminal boundary. It may
attach consumer-materialized `AttachmentContent` or provide one terminal
fallback message. It does not receive a raw notification envelope and does not
create another subscription.

Artifact candidate locators remain untrusted. The hook consumer owns validation,
filesystem authority, byte materialization, durable spool lifetime, retry, and
cleanup. Core and the Application adapter store neither candidate bytes nor
consumer delivery state. With no hook, existing adapter behavior is unchanged.

## Classification and evidence

- Preserving normalized message Metadata and keeping live-only observations
  out of completion checkpoints are Core fidelity invariants.
- Mapping native item/event shapes and producing bounded presentation text are
  Application-adapter policy.
- Typed App Server artifact-candidate extraction and ordered hook invocation are
  adapter-specific extension mechanics; materialization and lifetime remain
  consumer policy.
- Visibility defaults, commands, branding, and final rendering are consumer
  policy.

T3 activities provide a second recoverable item implementation. A T3
deployment without activities and an Application that emits no
`message.created` events remain valid counterexamples: the Gateway adds no
synthetic output and its existing completed-message behavior is unchanged.

## Consequences

- Consumers can preserve current commentary/tool/system visibility without a
  raw native side channel.
- Live-only presentation is ordered and bounded but intentionally not replayed.
- Completed checkpoints continue to name only authoritative history items.
- Adding an adapter Metadata key requires secrecy, cardinality, and boundedness
  review; it does not establish a new Core semantic.

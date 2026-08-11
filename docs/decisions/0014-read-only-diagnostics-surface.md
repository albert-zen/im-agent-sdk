# ADR 0014: Read-only diagnostics surface

- Status: Accepted
- Date: 2026-08-02

## Context

Long-lived Application transports and Gateway projection workers already know
facts needed to detect reconnect, bounded-queue overflow, worker degradation,
and recovery gaps. Logging those transitions is insufficient for consumers
that need a stable health view, while exporting native identifiers, exception
text, endpoints, paths, or protocol payloads would turn diagnostics into a
second data surface with unsafe cardinality and retention.

Codex/Zen App Server has a meaningful connection epoch and two dispatch lanes.
T3 uses independent HTTP request/response calls and has no equivalent
long-lived connection epoch. Long-polling Channels have distinct lifecycle and
worker evidence, while a request/response/webhook Channel may have none. These
counterexamples rule out making connection diagnostics a required Application
or Channel port.

## Decision

The SDK provides immutable, process-local diagnostic fact types and a
synchronous `Gateway.diagnostics()` read. The snapshot is:

- stable and versioned, but explicitly non-authoritative;
- read-only and computed without I/O, awaiting, callbacks, or persistence;
- bounded to configured Application instances, fixed queue names, aggregate
  projection counters, and allowlisted recovery-gap classifications;
- redacted: it excludes Thread/Conversation/request/route/native message IDs,
  content, error text, credentials, endpoints, paths, and attachments.

Application adapters may implement the structural `diagnostic_facts()` seam.
It is an optional adapter capability, not a required Core port. App Server
reports connection state/epoch, reconnect count, dispatch worker state, queue
capacity/depth/overflow, and a bounded last-failure code. T3 reports its
Application identity and no synthetic connection facts.

Channel adapters may implement the parallel structural `diagnostic_facts()`
seam. Channel facts preserve configured `channel_instance_id` and adapter kind,
never provider-supplied identity. QQ, Telegram, Feishu, and Weixin expose only
their process-local lifecycle/worker facts; QQ and Feishu additionally expose
the fixed `channel_inbound` queue. Provider failure, invalid shape, or identity
mismatch collapses to configured identity-only facts. A Channel without
meaningful long-lived state omits the provider rather than inventing health.

Gateway aggregates projection facts by count and classification. Existing
per-Thread worker health remains an internal troubleshooting API; the stable
snapshot does not reproduce its identities or free-form errors. All Native
Application state, including Thread, Turn, request, approval, and execution
state, remains authoritative in the Native Application.

OpenTelemetry is an optional consumer integration point. The SDK does not add
an exporter, HTTP endpoint, `health.json`, metrics label policy, polling
schedule, alerting, or operator UX. Consumers may poll the snapshot from their
own task and translate its bounded facts into their observability system.

## Consequences

- Reading diagnostics cannot block App Server socket reception or Channel
  delivery and does not create a hot-path observer cost when unused.
- Queue overflow counters are process-lifetime facts; snapshots are not a
  durable event log and reset on process restart.
- Consumers must treat absent connection facts as an honest adapter
  difference, not as an unhealthy connection.
- New fact values require a compatibility review for secrecy and cardinality.
  Unknown recovery-gap values collapse to `other`.
- Schema version 2 adds the optional per-Channel collection and the fixed
  `channel_inbound` queue name; version 1 had only Application, projection, and
  Gateway facts.

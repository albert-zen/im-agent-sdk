# Diagnostics component design

## Responsibility

Diagnostics exposes a stable, immutable view of process-local bridge health.
It helps a consumer answer whether SDK infrastructure is connected, bounded,
overflowing, retrying, or unable to reconstruct interactive-request state. It
does not answer whether a native Thread or Turn is healthy and is never an
authority for Agent state.

The public facts are:

- per configured Application: stable instance/kind plus optional connection
  facts;
- per configured Channel: stable instance/kind plus optional evidenced
  lifecycle/worker and fixed-name queue facts;
- per connection: lifecycle state, epoch, reconnect count, worker state, a
  bounded last-failure classification, and fixed-name queue facts;
- projection aggregate: worker lifecycle/degradation, restart, delivery
  failure, overflow, request-recovery degradation, and bounded gap codes;
- Gateway startup admission: capacity, current depth, lifetime overflow count,
  and current admission state;
- configured I1 inbound-content transformation: process-lifetime invocation,
  success, failure, timeout, cancellation, cancellation-overrun, and capacity-
  rejection counts plus one fixed last-failure classification.
- configured I2 inbound-failure presentation: the same fixed execution-count
  categories under its own typed facts, without exception, origin, rendered
  output, or delivery identity.
- configured Application A1 presentation: per-adapter process-lifetime
  invocation, success, omission, failure, timeout, cancellation, cancellation-
  overrun, and capacity-rejection counts plus one fixed last-failure category;
  facts/input/output, native item/event identity, and exception text are never
  retained. An absent presenter exposes no fabricated facts.
- configured O1 destination presentation: process-lifetime invocation,
  delivery, suppression, failure, timeout, cancellation, cancellation-
  overrun, and capacity-rejection counts plus one fixed last-failure category;
  message, route, delivery, policy output, and exception detail are absent. An
  absent policy exposes no fabricated facts.
- configured O2 delivery-outcome observation: process-lifetime notification,
  success, failure, timeout, cancellation, cancellation-overrun, and capacity-
  rejection counts plus one fixed last-failure category; message, destination,
  receipt, error detail, observer output, and exception text are absent. An
  absent observer exposes no fabricated facts.

Every collection is bounded by configuration or a fixed vocabulary. Native
resource IDs, route IDs, request IDs, message IDs, content, exception text,
credentials, endpoints, filesystem paths, and attachment metadata are absent.

## Ownership and extension

The immutable fact vocabulary and Gateway aggregation are SDK infrastructure.
Collecting a native transport fact is adapter policy. `diagnostic_facts()` is
a structural optional provider so an adapter without a long-lived connection
does not invent one and a third-party adapter does not need to change its Core
port implementation.

Codex and Zen share App Server connection facts. T3 is the second Application
counterexample: its HTTP request/response client returns `connection=None`.
The same rule applies to Channels: a future Channel provider must expose only
facts evidenced by its native transport; this ADR does not silently promote a
Codex transport model to Channel Core.

QQ, Telegram, Feishu, and Weixin provide the Channel-side evidence. Only QQ
and Feishu own an SDK process-local inbound queue, published under the fixed
`channel_inbound` name; Telegram and Weixin fabricate no queue. Configured
Channel identity/kind always wins over provider output. Missing, raising,
invalid, or mismatched providers fail closed to identity-only facts without
exception text.

## Snapshot semantics

`ImAgentGateway.diagnostics_snapshot()` is synchronous, side-effect free, and
does no native or repository I/O. Its `generated_at` is observation time,
`schema_version` describes the fact shape, and `authoritative=False` is
permanent semantic guidance. Repeated reads do not mutate counters.
Schema version 7 adds optional O2 outcome-observation execution facts to
Gateway facts. Schema version 6 added optional O1 destination-presentation execution facts to
Gateway facts. Schema version 5 added optional A1 presentation execution facts
to Application facts. Schema version 4 added optional I2 presenter execution facts; version 3 added
optional I1 transformer execution facts to the Gateway facts. Version 2 added
the optional Channel collection while preserving the
pre-Channel positional constructor order.

Projection details are aggregated before publication. Known SDK event gaps
retain a stable code; every other string maps to `other`. App Server overflow
updates counters in the existing non-blocking `put_nowait` path. Reading queue
depth uses local `qsize()` and never waits for a dispatcher.

## Consumer boundary

Consumers own polling, caching, HTTP/JSON rendering, `health.json`, dashboards,
alerts, OpenTelemetry instruments/exporters, labels, and degraded-product UX.
An OpenTelemetry bridge should poll from a separate consumer task. The SDK
does not invoke consumer callbacks from socket, projection, or Channel hot
paths.

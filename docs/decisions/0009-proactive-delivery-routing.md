# ADR 0009: Proactive delivery through one Gateway execution seam

Status: Accepted

## Context

An Agent task sometimes needs to send text or generated artifacts to the IM
Conversation that is observing its Thread without first receiving a new IM
message. Consumers currently solve this with product scripts that inspect
bridge state, copy Channel-native Conversation IDs, or read bot credentials.
That duplicates routing and security behavior outside the Gateway.

The same delivery can be retried after a timeout or process restart. Meanwhile
the remembered Thread route may move to another Conversation. Re-resolving the
route on every retry can therefore send one logical delivery to two people.
Conversely, silently retrying an ambiguous Channel outcome can duplicate a
native message on platforms without native idempotency.

ADR 0010 adds deterministic planning, bounded execution, backpressure, and
receipt-aware retry on this same input/result seam rather than creating a
second temporary delivery pipeline.

## Decision

### Typed delivery intent and target

Core defines one `DeliveryIntent` carrying a stable delivery ID, content,
optional reply reference, and one target:

- an explicit `ConversationDeliveryTarget`; or
- a `ThreadRouteDeliveryTarget`, optionally naming one exact route.

The target is routing intent, not a Channel-native send request. A Thread target
without an explicit route reuses the Gateway's configured projection policy:
`foreground_only` selects active bound routes, `remembered_last_recipient`
selects the retained recipient, and `all_observers` may select several routes.
An explicit route selector narrows the result without inventing a second
policy.

Gateway resolves the target once and records one or more
`DeliveryRouteSnapshot` values containing the Conversation, optional
Thread/route identity, route update time, and route-owned reply context. The
snapshot set is immutable for that delivery ID. A later retry returns the
recorded per-destination outcomes or explicit in-flight/unknown states; it
never follows a moved Thread route. A new delivery ID resolves the current
routes.

Repository identity derives from an SDK-controlled origin domain, trusted
principal identity, and the caller's delivery ID. The origin discriminator is
not authorizer-controlled, so an external principal cannot impersonate the
Gateway-internal namespace. The caller-facing root ID remains unchanged;
per-destination execution IDs derive from the namespaced submission identity.

### Scoped authorization before routing

The public proactive API requires an opaque credential and an injected
`DeliveryAuthorizer`. The authorizer returns a trusted `DeliveryPrincipal`
whose scope names allowed Threads and/or explicit Conversations. Untrusted
callers cannot submit their own effective scope.

The SDK provides a process-local scoped-capability authorizer as a reference.
Consumers may replace it with operating-system, service, or deployment
authentication. Credentials, bot secrets, and Channel-native Conversation IDs
are never stored in delivery content or exposed to an Agent task merely because
it can send through a Thread route.

Thread-targeted public results expose route IDs and sanitized receipts, not
the persisted `ConversationRef`, native message IDs, or free-form Channel
diagnostics. Explicit-Conversation callers may receive the same Conversation
identity they already provided.

Internal authoritative projection uses the same delivery executor after route
resolution but does not pretend to be an externally authenticated caller.

### Durable idempotency record, not a job queue

The delivery repository stores only:

- origin-and-principal-namespaced submission identity plus caller delivery ID;
- principal and target identity fingerprints;
- a canonical payload fingerprint;
- the resolved route snapshots;
- per-destination `in_flight`, `accepted`, `retryable`, `rejected`, or
  `unknown` state;
- typed per-destination Channel receipts and timestamps.

It does not store message or artifact content and is not a transcript or
durable delivery queue. Reservation is atomic. Within one principal namespace,
reusing a delivery ID with a different target, payload, or resolved destination
fails as a conflict. The same identity returns its existing typed result.

An `in_flight` record prevents a concurrent duplicate. If a process dies after
the Channel side effect but before completion, the surviving record is
ambiguous and is not automatically resent. A later retry is safe only after an
explicit retryable receipt; unknown outcomes remain sticky.

### Capability preflight and receipts

Gateway validates the complete intent before reserving or invoking a Channel:

- a configured destination Channel exists;
- content is non-empty;
- attachment support, source kind, count, declared size, and Channel limits are
  compatible when declared;
- proactive `LocalPath` content carries an authoritative SHA-256 identity that
  the Channel verifies while reading its trusted spool;
- one malformed artifact prevents every side effect.

The common planner owns capability-driven segmentation and grouping. Channel
adapters still own native upload, escaping, API encoding, and final platform
validation. `DeliveryReceipt` may contain per-content item receipts
so partial native success remains visible. Gateway never converts `unknown`
into accepted or hides a rejected item behind an aggregate boolean.

### One execution seam and reference ingress

Projection messages, interactive-request presentation, explicit Conversation
delivery, and Thread-route proactive delivery all converge on the same
Gateway-owned delivery executor and repository.

The SDK exposes a small transport-neutral JSON handler and reference
`imagent-send` HTTP client. The HTTP host remains consumer composition: it
mounts the handler in an existing authenticated local service and passes a
Bearer credential separately from the untrusted body. The CLI accepts only a
loopback HTTP(S) endpoint and reads the credential from a file or stdin. It
knows only that scoped credential, an Agent Thread reference or explicit
target, and the files it is sending. It does not read Gateway databases,
Channel state, or bot secrets.

The ingress authorizes the target before decoding inline base64 artifacts,
bounds the decoded bytes, creates a random exclusive directory beneath a
consumer-configured private staging root, writes SDK-controlled names, and
removes that directory after synchronous submission. It never accepts an arbitrary
server-side path. Thread-target responses omit resolved Channel-native
Conversation IDs.

The handler/CLI do not introduce a server framework, background worker,
durable spool, retry loop, or product environment-variable convention.

## Consequences

Consumers gain one safe proactive-send primitive without importing Channel
implementations or persistence details. Route movement is deterministic per
delivery identity, and ambiguous side effects remain explicit.

Products still decide how a local Agent process obtains a scoped credential and
where the JSON handler is mounted. Channel-native idempotency and throttling
remain adapter concerns. Capability planning, bounded queues, and conservative
retry follow ADR 0010 and consume the contracts established here.

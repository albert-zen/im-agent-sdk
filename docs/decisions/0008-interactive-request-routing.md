# ADR 0008: Interactive request routing without request ownership

Status: Accepted

## Context

Agent Applications can pause a Turn for an approval or structured user input.
An IM recipient needs to see that request and return a response, but the SDK
must not become the authority for request state, approval policy, sandbox
configuration, or who is allowed to approve.

One native request may be projected to more than one Conversation. A response
must not be accepted merely because its request ID is known: the originating
Conversation must have received that request. A transport reconnect can also
invalidate a native response handle even when the native Application preserves
other Thread state.

## Decision

### Typed semantic payloads

Core defines typed approval and user-input request payloads, typed request
resolution, and typed responses. A `RequestRef` scopes the opaque native
request ID by `ApplicationRef`; every event, operation, repository selector,
hash, uniqueness constraint, and state transition uses that same identity.
If a native transport reuses IDs after reconnect, its adapter includes the
connection epoch in the opaque native ID. The request also contains
Thread/Turn scope, prompt or questions, supported response shape, and native
expiry when known.

`request.opened` and `request.resolved` events carry these typed values rather
than hiding behavior-critical fields in Metadata. Application
`request.respond` remains the only native request mutation.

### Conversation response operation

A Channel-native action or optional text Controller submits one typed
Conversation response operation. Gateway validates the actual delivered
destination correlation and then creates the same Application
`request.respond` operation. Text, buttons, and cards do not create different
Core semantics.

Sender admission and product authorization happen before this operation.
Gateway verifies destination correlation; it does not infer that a sender may
approve tools, choose a sandbox, or grant Full Access.

### Minimal per-destination correlation

After a request delivery is accepted or known completed, Gateway stores one
minimal correlation per destination:

- application-scoped request, Thread, and Turn identity;
- Conversation and stable delivery identity;
- the minimal supported response shape: approval choice IDs or question
  cardinality/choice IDs;
- native expiry when present;
- the bridge projection state `open`, `responded`, `resolved`, or `stale`;
- creation/update times.

It does not store the prompt, answers, requested permissions, or a second copy
of native request state. The response shape is only bridge-owned routing
validation state: it proves what a delivered destination may submit and does
not assert that the native request remains pending. Correlations are
retention-bounded.

Request-scoped execution serializes local contenders. The native Application
remains first-writer authority. A second response, a resolved/stale request,
and a Conversation that never received the request fail with distinct stable
errors. A successful response transitions every destination correlation to
`responded`; the authoritative terminal event transitions them to `resolved`.
When a native terminal event races transport response acknowledgement, the
terminal state wins and response writeback cannot regress it.
A destination whose slow delivery finishes after either transition atomically
inherits the request-wide state and cannot create a new `open` response route.
For one epoch-scoped `RequestRef`, `stale` is also monotonic; transport reuse
must produce a new reference. Adapter-local terminal diagnostics are bounded.
If that window evicts an already-responded request before a request-ID-only
native resolution arrives, the adapter emits a stale projection while it
still has Thread/Turn scope and then forgets it.

### Projection and presentation

Open requests use the existing projection policy to choose active routes. A
replaceable Request Presenter renders one outbound request per route under the
same per-route delivery serialization as Agent messages. Correlation is
created only after that destination's stable delivery is accepted or already
completed. One route's failure does not authorize a response or block another
route.

Approval responses return the selected stable `choice_id`; Core does not
interpret native concepts such as once/session/cancel. User-input questions
declare minimum and maximum answer counts, available choice IDs, and whether
free text is allowed. Gateway validates responses against that stored shape.

The SDK ships a plain Markdown presenter and optional Slash response commands.
Channel-native presentation may use the same Presenter/typed-operation seam.
No common button/card envelope is admitted until at least two real Channels
prove one. Native prompt text is untrusted: the Markdown presenter renders
approval facts as an indented code block and escapes labels, descriptions,
and structured-input text. A question marked `secret` is not proof of a
secure Channel; the plain presenter emits only an unsupported notice and
creates no response correlation.

### Reconnect honesty

An Application adapter reconciles pending requests only when its native
endpoint exposes an authoritative pending-request snapshot. Otherwise a
connection reset invalidates transport-bound response handles, emits a stale
resolution for the projection, and rejects later response attempts. Gateway
does not manufacture pending requests from its correlations. Restored live
subscriptions are installed before native Application startup; only
correlations snapshotted before startup are candidates for no-snapshot
invalidation, so a request emitted by the new connection during startup is not
discarded as old state.

The Codex App Server mapping is based on the local native generated protocol
and transport tests. It covers command/file approvals, structured tool input,
and permission-profile approval. The raw permission profile remains in the
Codex adapter while a bounded human-readable summary is projected.

The IMZen integration supplies native Zen evidence that Zen emits the App
Server command-approval method and accepts its decision payload. Zen therefore
shares the typed App Server request runtime for that method while other request
kinds remain explicitly unsupported. Zen remains a distinct Application
adapter and authority. This does not promote Full Access, sandbox choice, or
product approval UX into Core. T3 remains unsupported until its native
response API is evidenced.

## Consequences

Full Access products can leave the capability idle. Multi-observer products
must choose their projection and sender-admission policy consciously; Core
does not select an approver. A crash between native Channel side effect and
durable correlation can still leave an unanswerable delivered prompt when no
authoritative request replay exists. ADR 0010 coordinates delivery receipts,
but cannot make that Channel/persistence boundary atomic.

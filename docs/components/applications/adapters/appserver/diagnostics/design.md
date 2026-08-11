# App Server diagnostics design

Component ID: `applications.adapters.appserver.diagnostics`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf owns the App Server-specific diagnostic provider: fixed redacted
connection/queue facts, process-local overflow/failure counters, and the
legacy debug summary/logging helpers used inside the adapter. It reports the
App Server connection epoch and independent dispatch lane state without
becoming an operator or Gateway health policy.

It does not own common diagnostic enums/value definitions, Application
diagnostic facts, Gateway diagnostics, polling, retry decisions, or consumer
presentation. Gateway and Application diagnostic values are imported from
their focused owners; the dependency-neutral connection/queue contracts are owned by
[`interaction.diagnostics`](../../../../interaction/diagnostics/design.md).
Only its App Server-specific positions belong here.

## Typed boundary and public facade

Inputs are local transport/runtime transitions and already received native
messages. `AppServerDiagnosticState.snapshot()` produces the ADR-0014
read-only, bounded, redacted `ConnectionDiagnosticFacts` surface. Snapshot
reads perform no I/O and retain their existing exact typed state, counter,
epoch, queue, and failure-code behavior.

The separate internal debug path uses exactly one bounded
`appserver.debug.v1` vocabulary. It is not an ADR-0014 fact surface, exporter,
or public context bag. Its records have a fixed record class and fixed fields:

- transport summaries carry only transport shape, normalized method
  category/kind/direction, error presence, one bounded structural body
  summary, and the validated preview policy;
- text summaries carry only a capped character length, a capped flag, an
  optional SHA-256 fingerprint, explicit `fingerprint_redacted` and
  `path_or_endpoint_redacted` flags, the validated preview limit, and a fixed
  `preview_emitted: false` marker;
- event records carry only allowlisted component/event/level/mode categories,
  a capped process-local connection epoch, bounded structural message/data
  summaries, and a capped ignored-field count; and
- health records carry only fixed connection/status/transport/ownership
  categories, booleans, capped epoch/retry counts, a health category, failure
  presence, and a capped ignored-field count.

Structural values use only fixed type, capped scalar-length, capped
collection-count, and at-most-four sample fields. Scalar lengths cap at
16,384 characters; collection and ignored-field counts cap at 64; and
process-local epoch/retry counters cap at 1,000,000. Mapping samples contain a
nullable SHA-256 fingerprint, capped length, and explicit `key_redacted` flag
for a key plus the sampled value's type and scalar length; sequence samples
contain only that value shape. An over-limit text or key, or a text/key that
looks like a path or endpoint, has no fingerprint: its redaction flag is true
and the fingerprint is `null`. The helper checks the scalar cap before any
path/endpoint scan or fingerprinting, examines at most four samples, and never
sorts or copies an unbounded native result, payload, permission, question,
change, event, or health mapping.
`max_preview_chars` is an integer that is neither `bool` nor non-positive and
may not exceed 256. The policy deliberately emits no raw preview for any
valid limit.

No debug record retains or emits raw native response/request/Thread/Turn/item
IDs, prompts/questions/answers, deltas/messages, commands, cwd, changed
paths, permission values, tokens/credentials, endpoint/userinfo, arbitrary
payload keys, or arbitrary payload values. A fingerprint is the only retained
text-derived value and is never a reversible prefix. Managed-media, single- or
embedded Unix paths, Windows drive/UNC paths, and endpoints/userinfo under any
URI scheme are redacted fail-closed before any helper logs or fingerprints
them. `emit_event` and `mark_appserver_health` normalize every supplied field
into this vocabulary; unknown fields are counted, never logged as keys or
values.

The vocabulary has deliberate fallback rules, rather than echoing an unknown
caller string: protocol shape/direction/category/kind use the fixed `unknown`
value (or fixed `invalid` after mapping rejection); adapter component, mode,
status, transport, and ownership use `other`; an unrecognized event becomes
`appserver.other`; and an unrecognized level becomes `DEBUG`. These strings
are finite diagnostics categories, never values copied from the native peer.

The formal export is now `AppServerDiagnosticState` from the exact owner
`imagent.applications.adapters.appserver.diagnostics`. The historical
`appserver_client` diagnostic modules are removed; no aggregate diagnostics
facade or duplicate implementation is introduced.

## Dependencies, state, and recovery

The provider depends on the Interaction connection/queue diagnostic contract
and internal App Server mapping classification. Counters and the current epoch are bounded
process-local state. Notification/server-request overflow is explicit and
connection-scoped; reset recovery is owned by the client/adapter, not by a
diagnostic exporter. ADR-0014 redaction applies to the fixed diagnostic facts;
the internal `appserver.debug.v1` rules above apply independently to every
debug helper and log sink.

## Current, target, and structural gap

The App Server diagnostic fact, summary, and runtime helpers now co-locate at
`src/imagent/applications/adapters/appserver/diagnostics.py`.
`src/imagent/applications/diagnostics.py` owns the Application fact contracts;
the historical cross-layer `imagent.diagnostics` module is absent. Common
connection/queue values come from
`imagent.interaction.diagnostics`.
Current evidence is `tests/applications/adapters/appserver/test_client.py`,
`tests/applications/adapters/appserver/test_transport_lifecycle.py`, and `tests/gateway/test_diagnostics.py`; the target suite is
`tests/applications/adapters/appserver/test_diagnostics.py`. The diagnostic
owner preserves the common contract and keeps Applications independent of
Gateway; the legacy debug path is now bounded/redacted rather than a separate
unbounded troubleshooting surface.

## Authority

- [App Server block](../README.md)
- [Interaction diagnostics design](../../../../interaction/diagnostics/design.md)
- [Gateway diagnostics design](../../../../gateway/diagnostics/design.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0014](../../../../../decisions/0014-read-only-diagnostics-surface.md)

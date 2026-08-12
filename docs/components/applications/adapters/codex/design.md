# Codex Application adapter design

Component ID: `applications.adapters.codex`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the Codex concrete Application adapter: native App Server
Project/Thread/Turn resource mapping, history/catch-up, input dispatch,
Codex's evidenced active-turn steer position, native request mapping, event
normalization/publication, and optional Codex live-activity and artifact
fact invocation. Codex native state remains authoritative.

It does not own product CWD/profile or Credits commands, Gateway binding or
delivery, request-correlation records, persistence, raw native events, a
second runtime/subscription/transcript, or consumer artifact bytes/path trust.
Codex live activity is a typed live-only A1 position; it is not recoverable
history and does not advance a completion checkpoint.

## Typed boundary and public facade

Inputs are a typed App Server client, fixed adapter configuration, common
Application operations/inputs, and optional typed Codex presenters/materializer.
Outputs are `ApplicationSummary`, typed operation results, `AcceptedTurn` or
explicit `ApplicationInputOutcomeUnknown`, canonical `AgentEvent` values, and
bounded typed presentation/artifact facts. No raw native envelope crosses the
Application boundary.

Fixed configuration includes required immutable `workspace_id` and `cwd`.
The adapter exposes one listable/readable workspace Project with the stable ID
and canonical-root fingerprint, scopes every Thread/event/history/request to
it, and reports native Project creation/deletion/switching unsupported.
It exposes or mutates a native Thread under that Project only after an
authoritative native read supplies the same Thread ID and matching canonical
`cwd`; missing or foreign scope evidence fails closed, including live and
interactive-request publication.

The current formal export is `CodexApplicationAdapter` from the lazy
`imagent.applications` facade, implemented at the exact target
`imagent.applications.adapters.codex:CodexApplicationAdapter`; the top facade
preserves object identity and lazy cold-import behavior. The private shared
App Server base is an explicitly mapped two-owner split candidate under
`imagent.applications.adapters.appserver._base`; it is not a public aggregate
adapter API. That base owns typed text/image preparation, verified local-image
epochs, the common typed pre-dispatch fence, `STARTED`/`CREATE_NEW` classification with
no expected Turn ID, and native `turn/start` dispatch shared by both leaves. It
does not own `steer_active_turn`, active-Turn selection, `STEERED`/
`PRESERVE_EXISTING` classification, or native `turn/steer`. Those
Codex-only positions live in this leaf.

## Dependencies, state, and recovery

Codex depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, the direct canonical
`applications.diagnostics` owner for Application diagnostic facts, four direct
App Server leaves (client, mapping, requests, and the separate adapter
diagnostics state owner), and the two presentation leaves. Transport remains
behind the App Server client rather than becoming a direct adapter dependency.
This leaf owns `steer_active_turn`, the
authoritative active-Turn read, `STEERED`/`PRESERVE_EXISTING` classification
with the active Turn ID, and native `turn/steer` dispatch. A candidate
active-turn read never authorizes fallback start. The common typed
pre-dispatch fence runs exactly once immediately before native mutation;
missing acceptance after native dispatch is unknown. Native history is the
recovery authority. App Server queue reset or presentation failure becomes an
explicit observation gap, not silent continuation or a second subscriber.

Codex App Server can return a valid new Thread before `thread/turns/list`
exists. The shared App Server owner records the exact successful create in one
bounded typed process-local evidence set. Before that Thread's first native
input dispatch fence or scoped turn-bearing native event, history/catch-up returns an empty
typed baseline without issuing the invalid native list call. This preserves
Gateway subscribe-before-baseline and lets the first input materialize the
Thread. The same evidence suppresses only the unavailable turn-bearing active-
Turn probe for that first input; the ordinary Thread scope read still runs.
The evidence is retired before native dispatch and on any turn-bearing
native event; thread-only creation/status notifications are not materialization
evidence. Eviction, adapter reconstruction, non-created Threads, and every later
or checkpointed recovery use strict native history. Neither native error text
nor Codex-specific Gateway policy participates.

## Current, target, and structural gap

Current code is `src/imagent/applications/adapters/codex.py` plus the explicitly
mapped private shared base
`src/imagent/applications/adapters/appserver/_base.py`. Adapter-owned evidence
is in `tests/applications/adapters/test_codex.py`,
`tests/applications/adapters/appserver/test_client.py`,
`tests/applications/adapters/appserver/test_mapping.py`,
`tests/applications/adapters/appserver/test_requests.py`, and the two
Applications presentation suites. The retained
`tests/applications/adapters/appserver/test_input_integration.py` and Gateway vertical/request-correlation
suites remain affected cross-component evidence. Codex owns the concrete
facade; Zen has no dependency on this module.

## Authority

- [Adapter block](../README.md)
- [Codex transition page](../../../application-adapters/adapters/codex.md)
- [Applications adapter overview](../../../application-adapters/design.md)
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

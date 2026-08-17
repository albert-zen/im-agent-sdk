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

Deployment-owned `thread_start_options` may provide App Server-native
sandbox/approval defaults for newly created Threads. The adapter copies the
mapping, rejects ambiguous aliases and attempts to replace its adapter-owned
`cwd` or the reserved native client `params` field, and does not persist the
mapping as SDK Thread state. A consumer whose conversation UX selects among
native profiles may call the concrete `create_thread_with_options` seam and
bind the returned authoritative `ThreadSummary` through the ordinary Gateway
operation; the profile remains client selection/configuration, not Codex or
SDK runtime state. The common `CreateThread` operation keeps the shared
control intent unchanged.

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
exists. The shared App Server owner records the exact successful create,
native connection epoch, stable session identity, and finite revisions
authorized by create or allowlisted non-Turn initialization facts in one
bounded typed process-local evidence set. Before that Thread's first native
input dispatch fence or allowlisted scoped turn-bearing native event,
history/catch-up returns an empty
typed baseline without issuing the invalid native list call. This preserves
Gateway subscribe-before-baseline and lets the first input materialize the
Thread. The same evidence suppresses only the unavailable turn-bearing active-
Turn probe for that first input; the ordinary Thread scope read still runs.
The evidence is retired before native dispatch and on allowlisted turn-bearing
native events; thread-only or unknown notifications with extraneous Turn IDs
are not materialization evidence. Stop/reset, epoch, session or non-authorized
revision change, same-ID recreation, eviction, adapter reconstruction,
non-created Threads, and every later
or checkpointed recovery use strict native history. Neither native error text
nor Codex-specific Gateway policy participates.

For the first input, Codex passes the exact evidence object as a generation
token; it never passes the preceding native Thread mapping as mutation
authority. After the asynchronous pre-dispatch hook, the shared owner performs
a fresh non-turn-bearing scope read and requires the same current connection
epoch, native session, authorized revision, and evidence-object identity at
the `turn/start` boundary. Reset/reconnect, an allowlisted Turn notification,
a supported turn-bearing server request, or same-ID replacement during the
hook invalidates that start and fails before native mutation. Successful
dispatch retirement names the expected object, so a stale plan cannot erase a
new same-ID generation. This does not authorize fallback start, retargeting, or
automatic retry, and it leaves Gateway's durable commit fence conservative.

Native notification dispatch is asynchronous with the create response, so a
queued `thread/started` may observe evidence that did not exist when the event
was admitted. Its partial or complete payload is not used as replacement
proof. For the evidence's connection epoch, the handler serializes on the exact
generation lock and reads the current native Thread without Turns. Exact
revision continuity preserves the generation. Because Codex can advance an
unmaterialized Thread's equal create/update/recency clock as creation settles,
the first event may authorize the bounded all-equal creation-clock revision
family after Thread/workspace, session, stable native identity, epoch, and
generation continuity are proved. Non-creation revision drift, read
failure, stale prior-epoch delivery, and an older handler racing a same-ID
successor remain strict and cannot retire or refresh the successor.

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
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

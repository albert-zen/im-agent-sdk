# Zen Application adapter design

Component ID: `applications.adapters.zen`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the Zen concrete Application adapter over the shared App Server
protocol: native Thread/Turn resources, history/catch-up, start/steer dispatch,
event normalization/publication, and the native request surface actually
evidenced by Zen (currently command approval). Zen native state remains
authoritative.

It does not own Codex live-activity semantics,
Zen product policy, Gateway binding/delivery/correlation, persistence, raw
native events, or a second runtime/subscription/transcript. Sharing the App
Server transport does not make an unevidenced Zen capability supported.

## Typed boundary and public facade

Inputs are a typed App Server client, fixed Zen adapter configuration, common
Application operations/inputs, and optional typed artifact materialization.
Outputs are common typed summaries/results, truthful `started/create_new` or `steered/preserve_existing`
input outcomes, canonical `AgentEvent` values, and bounded artifact facts when
configured. Native request and capability gaps fail explicitly.

Fixed configuration includes required immutable `workspace_id` and `cwd`.
The adapter exposes one listable/readable workspace Project with its typed
canonical-root fingerprint and uses its ProjectRef for all Thread/event/
history/request identity. Native Project management remains unsupported.
It exposes or mutates a native Thread under that Project only after an
authoritative native read supplies the same Thread ID and matching canonical
`cwd`; missing or foreign scope evidence fails closed, including live and
interactive-request publication.

Deployment-owned `thread_start_options` may supply Zen-native sandbox and
approval defaults for new Threads; the adapter copies them, owns `cwd`, and
does not persist them as SDK Thread state. A consumer with per-Conversation
presets may use the concrete `create_thread_with_options` seam and bind its
returned native Thread through Gateway; the preset remains client
selection/configuration, not Zen or SDK runtime state.

The current formal export is `ZenApplicationAdapter` from the lazy
`imagent.applications` facade, implemented at the exact target
`imagent.applications.adapters.zen:ZenApplicationAdapter`; the top facade
preserves the same object identity and lazy import behavior. The private
shared App Server base is an explicitly mapped two-owner split candidate under
`imagent.applications.adapters.appserver._base`; it is not a public aggregate
adapter API. Its input path is truthful start-only behavior: the shared base
owns typed text/image preparation, verified local-image epochs, the common
typed pre-dispatch fence, `STARTED`/`CREATE_NEW` classification with no expected
Turn ID, and native `turn/start`. The shared base has no steer configuration, active-Turn read,
`STEERED`/`PRESERVE_EXISTING` classification, or native `turn/steer`; Zen owns
its evidenced steering locally.

## Dependencies, state, and recovery

Zen depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, the direct canonical
`applications.diagnostics` owner for Application diagnostic facts, four direct
App Server leaves (client, mapping, requests, and the separate adapter
diagnostics state owner), and optional artifact materialization. Transport
remains transitive through the App Server client.
`prefer_active_turn` discovers the active Turn through a workspace-verified
native Thread read and uses Zen `turn/steer` with `expectedTurnId`. The concrete
`steer_active_turn=False` opt-out and `start_new_turn` preference use native
start. The typed pre-dispatch callback authorizes the exact Turn and immutable
reply correlation before mutation. A rejected steer never falls back to start.
Missing acceptance identity is unknown; a returned different Turn is handled
by the existing Gateway post-acceptance mismatch fence. A dispatched start with lost response is unknown, not an automatic
retry. History is native recovery authority; connection reset and
observation/materialization failure are explicit gaps.

## Current, target, and structural gap

Current code is `src/imagent/applications/adapters/zen.py` plus the explicitly
mapped private shared base
`src/imagent/applications/adapters/appserver/_base.py`. Adapter-owned evidence
is in `tests/applications/adapters/test_zen.py`,
`tests/applications/adapters/appserver/test_client.py`,
`tests/applications/adapters/appserver/test_requests.py`, the artifact
presentation suite, and
`tests/gateway/test_vertical_slice.py` for distinct Zen/Codex behavior. The
retained `tests/applications/adapters/appserver/test_input_integration.py` remains affected cross-component
evidence. Zen owns the concrete facade and has no dependency on Codex.

## Authority

- [Adapter block](../README.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

## Evidenced Zen input capability restoration

Zen's Codex-protocol implementation at commit `1702ca4` exposes `turn/steer`
with `expectedTurnId` and `image` with a base64 data URI. The Zen owner adopts
those native capabilities independently of Codex; it never imports the Codex
adapter or restores the retired aggregate adapter. The executable interop
scenario must run the public SDK adapter/client against the real Zen protocol.

When a trusted root is configured, Zen advertises `LocalPath` image sources.
It validates every image's finite declared byte size and supported media type,
acquires bytes through Interaction's no-follow descriptor chain, and sends
those exact bytes as an inline data URI. Per-input item and aggregate byte
bounds prevent unbounded acquisition. No native pathname is transmitted and
no shared-filesystem connection epoch is needed for inline bytes. Root, file,
size, or type failures precede native dispatch. A pathname replacement after
acquisition cannot change submitted bytes. The common base and Codex retain
the path-only rejection; the old pathname race is never restored.

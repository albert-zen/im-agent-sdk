# Zen Application adapter design

Component ID: `applications.adapters.zen`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the Zen concrete Application adapter over the shared App Server
protocol: native Thread/Turn resources, history/catch-up, start dispatch,
event normalization/publication, and the native request surface actually
evidenced by Zen (currently command approval). Zen native state remains
authoritative.

It does not own Codex active-turn steer or Codex live-activity semantics,
Zen product policy, Gateway binding/delivery/correlation, persistence, raw
native events, or a second runtime/subscription/transcript. Sharing the App
Server transport does not make an unevidenced Zen capability supported.

## Typed boundary and public facade

Inputs are a typed App Server client, fixed Zen adapter configuration, common
Application operations/inputs, and optional typed artifact materialization.
Outputs are common typed summaries/results, truthful `started/create_new`
input outcomes, canonical `AgentEvent` values, and bounded artifact facts when
configured. Native request and capability gaps fail explicitly.

The current formal export is `ZenApplicationAdapter` from the lazy
`imagent.applications` facade, implemented at the exact target
`imagent.applications.adapters.zen:ZenApplicationAdapter`; the top facade
preserves the same object identity and lazy import behavior. The private
shared App Server base is an explicitly mapped two-owner split candidate under
`imagent.applications.adapters.appserver._base`; it is not a public aggregate
adapter API.

## Dependencies, state, and recovery

Zen depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, four direct App Server
leaves (client, mapping, requests, and diagnostics), and optional artifact
materialization. Transport remains transitive through the App Server client.
`prefer_active_turn` remains a new native start until Zen
proves an equivalent steer mutation. A dispatched start with lost response is
unknown, not an automatic retry. History is native recovery authority;
connection reset and observation/materialization failure are explicit gaps.

## Current, target, and structural gap

Current code is `src/imagent/applications/adapters/zen.py` plus the explicitly
mapped private shared base
`src/imagent/applications/adapters/appserver/_base.py`. Adapter-owned evidence
is in `tests/applications/adapters/test_zen.py`,
`tests/applications/adapters/appserver/test_client.py`,
`tests/applications/adapters/appserver/test_requests.py`, the artifact
presentation suite, and
`tests/test_gateway_vertical_slice.py` for distinct Zen/Codex behavior. The
retained `tests/test_appserver_input.py` remains affected cross-component
evidence. Zen owns the concrete facade and has no dependency on Codex.

## Authority

- [Adapter block](../README.md)
- [Zen transition page](../../../application-adapters/adapters/zen.md)
- [Applications adapter overview](../../../application-adapters/design.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

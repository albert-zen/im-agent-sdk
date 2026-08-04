# App Server mapping design

Component ID: `applications.adapters.appserver.mapping`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf normalizes untrusted App Server JSON into internal mapping
facts used by the concrete Application adapters. It owns resource/list/page
shape checks, Thread/Turn/item/status/date normalization, protocol method
classification, and the internal `AppServerEvent` record used to route native
notifications and server requests inside Applications.

`AppServerEvent` is not a Gateway event contract and its native payload is not
exposed to Gateway or Controllers. Mapping does not own transport lifecycle,
JSON-RPC dispatch, request response policy, presentation/materialization,
Gateway projection, or product semantics.

## Typed boundary and public facade

Inputs are untrusted `Mapping[str, object]`/JSON values. Outputs include
normalized values such as `ThreadStatus`, `TurnStatus`, native IDs, strings,
item collections, or the internal classified event. `AppServerEvent.payload`
currently retains the native mapping inside Applications; generic collection
and text helpers do not impose finite bounds, and some missing event identities
normalize to empty values instead of failing closed. This leaf has no formal
public contract/export: raw native values must remain internal to the adapter
and may not cross into Gateway or Controllers.

The implementation now co-locates `native_object`, `native_list`,
status/ID/item/date helpers, `AppServerEvent`, method sets, and
`normalize_appserver_message` in one
`src/imagent/applications/adapters/appserver/mapping.py` owner. The two
historical modules are removed; no second normalizer or public mapping facade
is introduced.

## Dependencies, state, and recovery

The target mapping position feeds the Application contract, capabilities,
events, and Interaction message normalization used by Codex and Zen. It is
stateless: no native event sequence, history cache, checkpoint, subscription,
or Gateway state is created. Protocol classification must preserve explicit
supported, host-delegated, experimental, rejected, and unknown outcomes;
unsupported methods fail rather than being converted to a product command.

## Current, target, and structural gap

Current code and pure mapping evidence now live at
`src/imagent/applications/adapters/appserver/mapping.py` and
`tests/applications/adapters/appserver/test_mapping.py`. The affected
adapter/input evidence remains in `tests/test_appserver_input.py`. The
boundary still needs explicit text/collection limits and strict stable-identity
validation before mapping facts leave the native adapter position. That work
must preserve internal native ordering and must not expose
`AppServerEvent.payload` outside Applications; it is outside this mechanical
move.

## Authority

- [App Server block](../README.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0001](../../../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0015](../../../../../decisions/0015-typed-extension-seams-and-composition.md)

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

The current positions are `native_object`, `native_list`, status/ID/item/date
helpers in `src/imagent/applications/appserver_mapping.py`, plus
`AppServerEvent`, method sets, and `normalize_appserver_message` in
`src/imagent/applications/appserver_client/protocol_map.py`. The target is one
`src/imagent/applications/adapters/appserver/mapping.py` owner without a
second normalizer.

## Dependencies, state, and recovery

The target mapping position feeds the Application contract, capabilities,
events, and Interaction message normalization used by Codex and Zen. It is
stateless: no native event sequence, history cache, checkpoint, subscription,
or Gateway state is created. Protocol classification must preserve explicit
supported, host-delegated, experimental, rejected, and unknown outcomes;
unsupported methods fail rather than being converted to a product command.

## Current, target, and structural gap

Current code is split across
`src/imagent/applications/appserver_mapping.py` and
`src/imagent/applications/appserver_client/protocol_map.py`. Current evidence
is `tests/test_appserver_mapping.py` and the mapping portions of
`tests/test_appserver_input.py`; the target suite is
`tests/applications/adapters/appserver/test_mapping.py`. Beyond the two-file
physical split, the boundary needs explicit text/collection limits and strict
stable-identity validation before mapping facts leave the native adapter
position. That work must preserve internal native ordering and must not expose
`AppServerEvent.payload` outside Applications.

## Authority

- [App Server block](../README.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0001](../../../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0015](../../../../../decisions/0015-typed-extension-seams-and-composition.md)

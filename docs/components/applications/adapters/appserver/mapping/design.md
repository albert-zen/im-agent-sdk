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
is a validated, finite adapter-internal copy; it is never a raw callback
envelope and never crosses into Gateway or Controllers. This leaf has no
formal public contract/export.

The implementation now co-locates `native_object`, `native_list`,
status/ID/item/date helpers, `AppServerEvent`, method sets, and
`normalize_appserver_message` in one
`src/imagent/applications/adapters/appserver/mapping.py` owner. The two
historical modules are removed; no second normalizer or public mapping facade
is introduced.

## Native fact bounds and identity

The mapping owner applies these fixed adapter limits before copying a native
mapping/list or joining native text:

| Fact | Limit |
|---|---:|
| scalar native text | 16,384 characters |
| generic native list/collection | 256 values |
| native mapping | 64 keys; each key at most 128 characters |
| recursive native payload | 16 nesting levels and 4,096 total values |
| `item.content` aggregation | 64 parts and 16,384 aggregate characters |
| Thread, Turn, item, event, and textual request identities | 512 characters |

Native JSON object keys must be non-empty strings. Lists, mappings, depth, and
the total recursive shape are checked before their bounded copy is allocated;
content aggregation checks each part and the running total before
concatenation. Oversize or malformed facts raise only the fixed redacted
`invalid App Server native mapping` error. The mapper never truncates an
identity-bearing value into another identity.

The client keeps SDK dispatch context at the outer enriched callback envelope:
the JSON-RPC server-request `id` remains at its native root and the client
adds its connection epoch at that root. It never inserts either into native
`params`, so a native payload with exactly 64 keys remains valid. Native
`params` attempting to supply the reserved internal context keys
`_connection_epoch`, `_transport_request_id`, or `_request_id` fail closed;
they are not treated as transport evidence.

`MAX_NATIVE_TOTAL_VALUES` is an envelope-wide post-decode fan-out budget, not
a page truncator. The transport's separate #315 inbound-frame limit remains
the byte ceiling before JSON decode; this 4,096-value count bounds the typed
tree that mapping may walk and copy. The Application contract permits at most
20 history/catch-up turns, while lightweight resume asks for at most four
recent turns, so the budget reserves roughly 204 native values per requested
history turn or 1,024 per recent resume turn. It never discards values to fit:
a 4,097th value fails with the fixed error before a partial result can be
returned. Full Codex/Zen parity is the compatibility evidence; a real native
shape that needs more capacity requires changing this explicit limit and its
tests/docs together, rather than adding truncation.

Required identities preserve their exact native string value: blank,
non-string/non-scalar, or overlong values fail closed rather than becoming an
empty string. When the same semantic identity appears through aliases, every
provided value must agree exactly: resource `id`/`threadId`, `id`/`turnId`, and
`id`/`itemId`; outer payload and nested Thread/Turn/item identities; matching
server-request transport IDs; and `eventId`/`event_id`. A conflict is a fixed
mapping failure, never a first-alias-wins choice. The requirement is
method-specific:

- typed Thread resources require a Thread ID; typed history/acceptance needs
  the Thread, Turn, and item IDs that its resulting fact carries;
- `item/completed` requires Thread, Turn, and item identity before a canonical
  message or artifact fact; `turn/completed` requires Thread and Turn;
- `item/agentMessage/delta` requires Thread and Turn identity but remains an
  unprojected transient delta when its native event ID is absent; configured
  Codex A1 live activity additionally requires a native event ID before it can
  invoke a presenter or emit `message.created`;
- a supported App Server server request requires transport request, Thread,
  and Turn identity; `serverRequest/resolved` requires its request identity.

Valid unknown methods remain classified as unknown and do not acquire an
unrelated identity requirement. Canonical event IDs are deterministically
derived from validated stable identities, not text, timestamps, or raw
payloads. A configured A1 fact that would drive outbound idempotency uses its
required native stable event ID; it never uses a UUID or other process-local
substitute.

## Dependencies, state, and recovery

The target mapping position feeds the Application contract, capabilities,
events, and Interaction message normalization used by Codex and Zen. It is
stateless: no native event sequence, history cache, checkpoint, subscription,
or Gateway state is created. A resource/history mapping failure returns the
fixed redacted error before a typed result is assembled. A notification mapping
failure creates the fixed `application_native_mapping_failed` live-recovery
gap before downstream event/presenter/materializer dispatch; an invalid server
request receives the fixed invalid-params rejection before request opening.
Protocol classification preserves explicit supported, host-delegated,
experimental, rejected, and unknown outcomes; unsupported methods fail rather
than being converted to a product command.

## Current, target, and structural gap

Current code and pure mapping evidence now live at
`src/imagent/applications/adapters/appserver/mapping.py` and
`tests/applications/adapters/appserver/test_mapping.py`. The affected
adapter/input/event evidence remains with the Codex, Zen, request, and
presentation suites. The single mapping owner preserves internal native event
order and must not expose `AppServerEvent.payload` outside Applications; no
second normalizer is introduced in a concrete adapter, request runtime, or
Gateway.

## Authority

- [App Server block](../README.md)
- [ADR 0001](../../../../../decisions/0001-contract-and-resource-foundations.md)
- [ADR 0015](../../../../../decisions/0015-typed-extension-seams-and-composition.md)
- [ADR 0016](../../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

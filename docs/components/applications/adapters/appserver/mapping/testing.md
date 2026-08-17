# App Server mapping testing

## Current evidence

`tests/applications/adapters/appserver/test_mapping.py`
(`AppServerMappingTests`) is the focused normalization suite. It covers native
containers, status aliases, unknown shapes, Thread/Turn/item content, errors,
datetime normalization, and the finite native-fact boundary.
`tests/applications/adapters/appserver/test_input_integration.py` supplies affected adapter evidence for Thread
profiles, identity failures, local-image epochs, and Codex/Zen dispatch policy.

The mapping suite proves exact-limit success and limit-plus-one fixed-redacted
failure for scalar text, generic lists, mapping keys, recursive total values,
and content aggregation. It also proves missing, blank, non-scalar, overlong,
or conflicting method-required Thread/Turn/item/request identities fail before
a typed fact is returned. A Turn-scoped delta without native Turn identity
terminates live observation with the fixed native-mapping recovery gap. The
conflict cases cover inner/outer item and Turn
identity, resource aliases, and `eventId`/`event_id`.
Affected Codex/Zen/request/event suites prove no canonical event, request open,
history entry, presenter, or materializer dispatch follows the failure,
including a conflicting outer/nested item ID before artifact materialization;
valid unknown methods remain unknown, and valid native ordering/parity remains
unchanged. Raw `AppServerEvent` payloads never leave Applications.
The client/transport cross-component evidence also proves dispatch preserves
an exact 64-key server-request or notification `params` mapping unchanged,
while 65 keys fail at mapping; SDK connection context stays at the enriched
envelope root and forged reserved payload context fails closed.

## Target evidence and gates

Run the target pure-mapping suite and the affected adapter/input evidence with:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.appserver.test_mapping -v
uv run python -m unittest tests.applications.adapters.appserver.test_input_integration -v
```

The full adapter block also runs unittest discovery, compile/schema/doc-link,
ruff/format/pyright, component-map, AgentKit, and clean-wheel verification.

## Authority

- [Mapping design](design.md)
- [Application events design](../../../events/design.md)

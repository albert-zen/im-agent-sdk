# Queued Gateway tests

`tests/integration/test_queued_gateway_native.py` is the public vertical gate.
With IMAGENT_ZEN_ROOT selecting the reviewed Core main tree, it uses authenticated
production WebSocket client, actual native queue mutations, synthetic model and
Channel, and fresh SQLite. It proves bounded first/active admission, original
reply identity, canonical cancellation, actor/Conversation denial, shared-capacity
rejection, reconstruction with changed defaults, binding-clear notice suppression,
and approval routing/response. No real credentials, accounts or databases enter it.

`tests/gateway/persistence/test_queued_input_store.py` runs Memory/SQLite parity,
real transaction rollback, committed acknowledgement loss, stale owners, shared
capacity, monotonic merge, original Turn correlation, migration and schema leakage.

`tests/gateway/input/test_queued_gateway_fencing.py` adds adversarial public queue
and presenter boundaries. Unknown sends must remain single-dispatch across
redelivery/reconstruction/config changes; missing or conflicting canonical evidence
must not invent admission or a Turn. Queued read is not subscription. Native
observer cancellation/epoch/overflow/per-Thread resume ownership is tested by the
Application-owned `test_zen_queue_lifecycle.py` and opt-in
`test_zen_queue_native_lifecycle.py`.

Default steer tests remain mandatory. Focused/native passes do not substitute for
full unit, typing, schema, component-map and independent review gates.

`test_queued_contract.py` checks exact public facade ownership, bounded immutable
status, closed status/origin/correlation schemas and rejection of content, paths,
raw actors, native failure text and retry scheduling fields.

`test_prior_v1_queue_upgrade.py`, with IMAGENT_PRIOR_V1_SOURCE pointing to an
actual pre-queue SDK checkout, creates the synthetic database in an independent
old interpreter. It compares every prior table schema and row before/after the
additive migration, then checks old protected claims/effects and a new queued
receipt across another reopen. Without that source it explicitly skips.

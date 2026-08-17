# Troubleshooting typed failures

Start with the public result variant, fixed error code, or fixed diagnostic
fact. Never branch on provider exception text: native text is not a stable SDK
contract and is intentionally excluded from durable receipts and diagnostics.

Expected runtime action failures are values:

```python
from imagent import Failed, OutcomeUnknown, Partial, Succeeded

result = await action()
if isinstance(result, Succeeded):
    use(result.value)
elif isinstance(result, Partial):
    preserve_committed_value(result.value, result.error.code.value)
elif isinstance(result, OutcomeUnknown):
    stop_automatic_retry(result.error.code.value)
elif isinstance(result, Failed):
    handle_proven_failure(result.error.code.value)
```

Configuration/programmer violations may raise before work starts. Input,
delivery, lifecycle, and adapter boundaries also expose their documented typed
exceptions or result enums. Diagnose them at the owning layer below.

## Gateway action outcomes

Owner: scoped actions, effect execution, and the coherent Gateway store.

### `conflict`

The same stable action ID was presented with different semantic intent, or a
stored immutable delivery reservation disagrees with the new request. Keep the
original ID/payload pairing or allocate a new ID for genuinely new intent.
Do not delete receipts or alter bridge rows to force reuse.

### `capacity_exhausted`

A finite owner rejected new work before the relevant side effect: effect
receipts, idempotency, projection workers, command handlers, or delivery
submissions. Identify the owning capacity from the operation and fixed
diagnostic counters. Drain work or change a reviewed deployment limit; do not
replace the bound with an unbounded collection.

### `stale_runtime`

Shutdown, lease loss, or projection lifecycle termination won the fence. If a
new action failed, no durable route mutation was authorized. If the result is
`Partial`, preserve its committed value and replay the identical action only
after constructing a fresh running Gateway. Never reuse a stopped Gateway.

### `stale_binding`

The binding generation, Application registration, or authoritative Project /
Thread ancestry changed. Read the current binding through
`ConversationActions.get_binding()` and read the resource through the scoped
Application surface. Ask the user or product policy to select again; Core must
not guess a replacement.

### `unsupported`

Capability preflight says the adapter cannot perform the operation. Treat this
as a normal product branch. Do not probe undocumented native methods, invent a
fallback mutation, or add a pre-v1 compatibility path.

### `native_rejected`

The Application returned a known terminal rejection. Present product-specific
guidance using the fixed code and separately observed native operator state.
The SDK does not translate arbitrary provider strings into policy.

### `store_failure`

The coherent store could not prove the intended transaction. Stop new work,
preserve the original action identity, and diagnose store durability,
permissions, disk capacity, and lease ownership outside SDK diagnostics. Do
not combine Memory and SQLite owners or bypass fencing.

### `native_outcome_unknown` / `OutcomeUnknown`

A native side effect may have happened after its durable fence. Never retry it
under a new action ID and never infer absence from `not_found`. Same-ID replay
is allowed only to read/reconcile the durable receipt through evidenced native
idempotency or terminal operation status.

### `Partial`

An earlier effect is authoritative even though a later phase failed. Preserve
`result.value`. For create-and-bind, never compensate by deleting the created
resource; resolve the binding failure explicitly. For a known durable route
with failed process-local activation, replay the identical action after the
runtime is healthy.

## Gateway input and routing failures

Owner: inbound admission, binding resolution, dispatch, and projection.

### `missing_binding`

No complete Application → Project → Thread selection exists. This fails before
content transformation, route mutation, or native input. Run an explicit
consumer onboarding flow with scoped actions; do not auto-select a sole
Application or default CWD.

### `stale_binding`

A complete stored binding no longer resolves against the registered
Application or authoritative resource. Present re-selection. A changed
fixed/flat workspace root requires a new workspace ID, not bridge-row repair.

### `GatewayStartupOverflow` or `GatewayNotRunning`

Startup admission is full or closed. The Channel must not bypass admission or
call the Application directly. Verify lifecycle ownership and startup bounds,
then redeliver only when the durable inbound identity remains safely
reclaimable.

### `EventStreamGap`, `CursorExpired`, or `ProjectionRecoveryUnavailable`

The affected Thread lost live position or cannot complete bounded recovery.
Gateway supervises resubscription and authoritative reconciliation. Inspect
fixed recovery gap codes; do not create an SDK event journal, scan an
unbounded archive, or redispatch accepted input.

### projection or serialization capacity

`ProjectionWorkerCapacityError` and keyed-serialization capacity reject a new
distinct identity while existing owners continue. Reduce concurrent active
Threads/keys or review the explicit limit. A same-Thread join must not be
worked around with a second subscription.

## Application and interactive-request failures

Owner: the native Application for resource/request truth; Gateway only for
delivered-destination correlation.

### `not_found`

The authoritative resource is absent at the read/preflight boundary. If it is
bound, treat the binding as stale. Absence is never proof that an older fenced
native mutation could not still commit.

### `unauthorized_destination`

The responding Conversation did not receive an accepted request presentation.
Do not authorize by request ID alone. Re-present only through the ordinary
projection path if native pending truth still exists.

### `request_duplicate`

A competing response already won. Read native request truth and present the
terminal state; do not submit a second choice.

### `request_resolved`

The Application has terminal resolution truth. Gateway correlation cannot
reopen it, and `request.resolved` does not itself end the Turn.

### `request_stale`

The response handle is no longer proven usable, commonly after reconnect
without an authoritative pending-request snapshot. Do not reconstruct pending
state from the Gateway correlation.

### Application adapter unavailable or `adapter_failure`

Use the adapter's fixed connection/queue diagnostic facts and its native
operator surface. Credentials, endpoints, sandbox/Full Access, and provider
retry policy remain deployment or consumer concerns, not Gateway state.

## Media, artifact, and delivery failures

Owner: Interaction for typed source/trust, the accepting adapter for native
I/O, Gateway for planning/coordination, and the consumer for stored bytes.

### unsupported or invalid attachment

Check the declared source kind, media type, count, size, grouping, lowercase
SHA-256, and explicit trusted root. A path or URL in Metadata grants no trust.
Reject before native work; do not add an unrestricted downloader.

### materialization capacity, timeout, cancellation, or failure

The App Server A1 materializer failed its finite typed contract. Use fixed
Application artifact diagnostic codes. Diagnose the consumer-owned byte store,
quota, ledger, and startup sweep separately; the SDK has no artifact spool.

### `DeliveryAuthorizationError`

The proactive credential is absent, invalid, revoked, or out of scope.
Authentication must precede route resolution and artifact acquisition.
Credential issuance/storage/rotation remains consumer-owned.

### `DeliveryPlanningError`

The complete logical message is not representable by the Channel's declared
capabilities. Change the content or correct the adapter capability profile;
do not partially send after failed preflight.

### `rejected`, `retryable`, `partial`, or `unknown` receipt

Inspect the typed aggregate and per-item/per-destination receipts. Retry only
an explicitly persisted `retryable` result, with the same delivery ID and
pinned destination snapshot. Preserve accepted prefixes. Never resend
`unknown` or reinterpret it from provider text.

## Store and lifecycle failures

Owner: Gateway persistence and the consumer's process/storage operations.

### `RuntimeLeaseUnavailable`

Another Gateway owns the same `gateway_id`/store namespace. Stop it cleanly or
wait for store-authoritative expiry. Do not run active-active or edit the lease.

### `StaleRuntimeFence`

An old owner attempted a mutation after lease loss/takeover. Terminate that
runtime and use the current owner. Lease takeover cannot revoke a native call
already fenced, so its outcome may remain unknown.

### `WorkspaceIdentityConflict`

A fixed/flat workspace ID now maps to another canonical root fingerprint.
Restore the configured root or deploy a new immutable workspace ID so old
bindings become explicitly stale.

### `GatewayLifecycleFailure`

Use the fixed owner/type cleanup evidence to locate the Channel, Controller,
Application, extension, or store owner that did not close. Do not increase the
join bound indefinitely or start a replacement before lease closure/expiry.

## Reading diagnostics safely

`gateway.diagnostics()` is synchronous, bounded, redacted, process-local, and
non-authoritative. It reports fixed states, gap/failure enums, and aggregate
counters only. It deliberately excludes content, IDs, paths, endpoints,
credentials, and exception text. Use native Application tools for Agent truth
and consumer monitoring for deployment/storage/credential truth.

See the focused [diagnostics recipe](../recipes/diagnostics.md) and the
[typed outcome design](../components/gateway/outcomes/design.md).

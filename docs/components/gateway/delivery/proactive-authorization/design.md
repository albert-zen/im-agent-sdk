# Gateway proactive authorization design

## Purpose and boundary

`gateway.delivery.proactive-authorization` converts an opaque credential into
a validated, typed `DeliveryPrincipal`. The principal carries only stable
Thread and Conversation scope. Proactive delivery must authenticate before
route resolution or Channel work, and untrusted callers cannot supply their
own effective scope.

The leaf owns the `DeliveryAuthorizer` Port, `DeliveryPrincipal` scope value,
validation, the process-local `ScopedDeliveryAuthorizer` reference
implementation, and fixed authorization failure. It does not own credential
persistence, HTTP authentication policy, IM admission, Agent sandbox policy,
route selection, or delivery execution.

The contract and Port definitions physically live in this leaf. The historical
`imagent.adapters` and `imagent.contracts` modules are absent; there is no
second definition or alternate authorization path.

## Reference registry

The reference registry is explicit, instance-local, and bounded.
`ScopedDeliveryAuthorizer(*, max_principals: int = 4096)` accepts only a
positive non-`bool` integer capacity. The capacity identity is the opaque
credential string already used as the sole `_principals` key; it is not a
principal ID, scope, target, content, or time value.

`issue` validates the principal, accepts either a caller-provided non-empty
opaque credential or a cryptographically random token when none/empty is
supplied, rejects values over 4096 characters, and fails duplicate
registration. Its duplicate lookup, capacity check, and insertion share the
same async lock as `revoke` and `authenticate`. Duplicate detection has
priority at capacity: reissuing a registered credential keeps the fixed
duplicate error, while a distinct credential at capacity fails before mutation
with the fixed redacted `ValueError("delivery credential registry capacity is exhausted")`.

Capacity never prevents existing access or exact removal: `authenticate` and
`revoke` continue to operate for a registered credential while the registry is
full, and a successful revoke releases only that credential's slot. A task
cancelled while waiting for the lock has not entered the mutation and leaves
no partial registration. `revoke` remains idempotent and `authenticate`
returns only the registered immutable principal or a fixed authorization
error.

Registry contents and their finite capacity are process-local by design; a
fresh process starts with an empty registry. Production consumers own
credential issuance, storage, rotation, revocation recovery, and any external
identity provider. The SDK does not persist secrets or infer credentials from
Channel/Application state.

## Public surface

`imagent.gateway.delivery` is the finite target facade for
`DeliveryAuthorizer`, `DeliveryPrincipal`, `ScopedDeliveryAuthorizer`, and
`validate_delivery_principal`. No cross-layer aliases are retained. The
Gateway authorizer is the only code that owns the reference registry and its
fixed authorization failure.
Canonical `Gateway.authorize_proactive_target()` exposes that check before
caller-owned artifact acquisition, while `deliver_proactively()` repeats it at
submission. Credentials remain process-local/downstream and never enter the
coherent Gateway store.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [Gateway design](../../design.md)
- [Channel contract](../../../interaction/channels/channel-contract/design.md)

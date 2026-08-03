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

## Reference registry

The reference registry is explicit and instance-local. `issue` validates the
principal, accepts either a caller-provided non-empty opaque credential or a
cryptographically random token when none/empty is supplied, rejects values
over 4096 characters, and fails duplicate registration. An async lock
serializes issue, revoke, and authenticate. `revoke` is idempotent and
`authenticate` returns only the registered immutable principal or a fixed
authorization error.

Registry contents are process-local by design. Production consumers own
credential issuance, storage, rotation, revocation recovery, and any external
identity provider. The SDK does not persist secrets or infer credentials from
Channel/Application state.

## Public surface

`imagent.gateway.delivery` is the finite target facade for
`DeliveryAuthorizer`, `DeliveryPrincipal`, `ScopedDeliveryAuthorizer`, and
`validate_delivery_principal`. Existing formal `imagent.adapters` and
`imagent.contracts` exports remain exact aliases while their broader Port and
state-contract splits are pending; no second implementation is retained.

## Authority

- [ADR 0009](../../../../decisions/0009-proactive-delivery-routing.md)
- [Gateway design](../../design.md)
- [Ports design](../../../ports/design.md)

# Gateway persistence effect contracts design

Component ID: `gateway.persistence.effects`

Parent: `gateway.persistence`

## Purpose

This leaf owns the passive durable-action vocabulary shared by the store and
effect executor: namespaced action identities and fingerprints, fixed receipt
categories/phases, bounded errors and values, store mutation plans, native and
create-binding workflow requests, receipt records, and their validators/codecs.

Effective action identity is namespaced by Gateway, trusted principal,
optional Conversation, action kind, and caller-stable action ID. Payload and
phase hashes use distinct domains. Only bounded scalar/tuple payload facts may
be fingerprinted, and persistence retains digests plus minimal stable resource
references—not raw principal, arguments, content, CWD, paths, credentials, or
native payloads.

These are passive contracts. The leaf does not acquire a lease, execute a
transaction, call an adapter, expose public action ergonomics, select retry
policy, or reconcile native truth. `gateway.persistence.gateway-store` owns
durability and `gateway.effect-execution` owns the state machine.

`StoreMutationPlan.conversation_ref` is always explicit and independent of a
binding target. This lets observation and clear-observation mutate routes
without manufacturing a binding write or advancing the binding generation.
Hierarchical clear uses the closed `BindingClearScope` (`thread`, `project`,
or `application`) rather than a caller-pre-read `BindingTarget`. The store
derives retained ancestors inside the receipt/CAS transaction; replacement
and clear intent are mutually exclusive.
Any binding target or route in the plan must belong to that exact
Conversation. Persisted errors combine the fixed `ActionErrorCode` with, when
needed, the existing closed common `OperationErrorCode`; arbitrary strings are
not accepted or stored.
Binding and expected generations are strict non-Boolean, non-negative integers
in plans, values, receipts, and codecs. A post-native-fence callback that
returns any other shape is ambiguous and becomes sticky `outcome_unknown`;
it cannot become a terminal success that later fails to decode.

## Authority

- [V1 design](../../../../V1_DESIGN.md)
- [V1 executable specification](../../../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../../../decisions/0016-uniform-workspace-and-consumer-actions.md)

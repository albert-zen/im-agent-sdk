# Startup preparation design

Component ID: `gateway.startup-preparation`

This leaf owns the immutable `StartupPreparation` configuration and the narrow
`StartupConversationActions` binding read/clear surface. Gateway lifecycle owns
ordering, bounded invocation, and rollback; ordinary scoped actions and the
coherent store remain the only durable mutation owners.

See [ADR 0017](../../../decisions/0017-startup-conversation-preparation.md) for
the concrete product counterexample, authority, absence, scope and failure
contract. Public values are defined in `imagent.gateway.startup`; no generic
callback context, Gateway factory, native API, or repository is exposed.

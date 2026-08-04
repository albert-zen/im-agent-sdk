# App Server client design

Component ID: `applications.adapters.appserver.client`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf owns the reusable App Server JSON-RPC client boundary: configured
target parsing and supervision, stdio/WebSocket selection, bounded request and
callback dispatch, retry/backoff coordination, connection epochs, and the
immutable callback-admission fence. It is protocol infrastructure consumed by
the Codex and Zen adapters; it is not an Application resource owner.

It does not own native Project/Thread/Turn or transcript mapping, request or
approval policy, Gateway recovery, IM route correlation, product launcher
configuration, or raw native event exposure. A callback handler is an internal
typed consumer position; its native payload never crosses to Gateway.

## Typed boundary and public facade

Inputs are configured target/supervisor values and bounded JSON-RPC method and
parameter mappings. Outputs are `AppServerResponse` values carrying a decoded
JSON result and an immutable `AppServerDispatchPosition`; ordinary failures
are `AppServerError`. Notification and server-request handlers receive the
client's internal JSON mapping at the adapter boundary only.

The current formal exports are `AppServerClient`, `AppServerDispatchPosition`,
`AppServerError`, `AppServerResponse`, `AppServerSupervisor`,
`APP_SERVER_DISPATCH_POSITION_KEY`, and `codex_app_server_client` through the
historical `imagent.applications.appserver_client` facade, with the factory
also exposed by `imagent.applications`. The target package exposes the exact
same objects from `imagent.applications.adapters.appserver.client`; no second
client or compatibility implementation is planned.

## Dependencies, state, and recovery

The client depends on the transport leaf, its target/supervisor values, and
the Applications diagnostic fact surface. It may use the Application input
outcome type for honest post-dispatch classification, but it does not import
Gateway. Notification and server-request queues are independent and finite.
Connection reset increments the epoch, invalidates transport-bound pending
calls and request handles, and is reported to the concrete adapter for an
explicit observation gap. The dispatch position is wire-admission order, not
an Application event sequence or replay cursor. Bounded retry/supervision
does not make an unknown native mutation safe to redeliver.

## Current, target, and structural gap

Current implementation is split across:

- `src/imagent/applications/appserver_client/__init__.py`
- `client.py`, `handoff.py`, `retry.py`, `supervisor.py`, and `target.py`

The target is `src/imagent/applications/adapters/appserver/client/`, with the
finite owner facade at its package boundary. Existing evidence is
`tests/test_appserver_client.py` and the client-facing portions of
`tests/test_appserver_transport.py`; the target focused suite is
`tests/applications/adapters/appserver/test_client.py`. The current `client.py`
is a large cohesive dispatch implementation and must be split only in a later
mechanical slice that preserves the ordering/recovery state machine.

## Authority

- [App Server adapter block](../README.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0004](../../../../../decisions/0004-event-fanout-and-recovery.md)
- [ADR 0013](../../../../../decisions/0013-bounded-application-event-admission.md)
- [ADR 0015](../../../../../decisions/0015-typed-extension-seams-and-composition.md)

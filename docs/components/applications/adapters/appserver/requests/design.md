# App Server requests design

Component ID: `applications.adapters.appserver.requests`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf maps only native App Server server requests with evidence from the
Codex and Zen integrations. It owns `PendingAppServerRequest`, supported
method-specific request values, native response payload fidelity,
`ServerRequestMapper`, and the bounded `AppServerRequestRuntime` that opens,
responds to, resolves, stales, and diagnostically retains request handles.

It does not own approval/permission policy, Gateway route authorization,
presentation, persistence, a pending-request snapshot that the native server
does not provide, or invented Zen request kinds. T3 remains explicitly
unsupported here.

## Typed boundary and public facade

Inputs are bounded native server-request mappings containing method, stable
transport request ID, Thread/Turn identity, and connection epoch; responses
are typed `RequestResponse` values validated against the derived request
shape. Outputs are canonical `InteractiveRequest`/Application request events
and the exact native JSON response or typed request error.

The formal contracts are `PendingAppServerRequest` and
`AppServerRequestRuntime` from the exact target facade
`imagent.applications.adapters.appserver.requests`; the mapper alias and
`UnsupportedAppServerRequest` are internal typed positions. The historical
modules are removed, preserving exact objects and no second request contract.

## Dependencies, state, and recovery

The leaf depends on Applications contract/events/operations/requests and the
App Server client. Request IDs are scoped by Application instance, connection
epoch, and native transport ID. A reset stales active and responded handles
when no authoritative pending snapshot exists. Response/resolve races use
first-writer native authority; duplicate, resolved, stale, invalid-shape, and
unsupported cases retain their current explicit errors. The terminal outcome
cache is finite and process-local; no request truth is persisted by the SDK.

## Current, target, and structural gap

The co-located implementation is
`src/imagent/applications/adapters/appserver/requests.py`. Adapter-owned
evidence is in `tests/applications/adapters/appserver/test_requests.py`,
including the Codex/Zen wire fixtures, race/reset, bounds, and diagnostics
cases. The retained `tests/test_appserver_requests.py` suite contains only the
Gateway request-correlation/presenter integration evidence. The structural
move is complete without changing request epoch, response-shape, or
first-writer behavior.

## Authority

- [App Server block](../README.md)
- [Applications request design](../../../requests/design.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0008](../../../../../decisions/0008-interactive-request-routing.md)

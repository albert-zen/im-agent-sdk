# Codex Application adapter design

Component ID: `applications.adapters.codex`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the Codex concrete Application adapter: native App Server
Project/Thread/Turn resource mapping, history/catch-up, input dispatch,
Codex's evidenced active-turn steer position, native request mapping, event
normalization/publication, and optional Codex live-activity and artifact
fact invocation. Codex native state remains authoritative.

It does not own product CWD/profile or Credits commands, Gateway binding or
delivery, request-correlation records, persistence, raw native events, a
second runtime/subscription/transcript, or consumer artifact bytes/path trust.
Codex live activity is a typed live-only A1 position; it is not recoverable
history and does not advance a completion checkpoint.

## Typed boundary and public facade

Inputs are a typed App Server client, fixed adapter configuration, common
Application operations/inputs, and optional typed Codex presenters/materializer.
Outputs are `ApplicationSummary`, typed operation results, `AcceptedTurn` or
explicit `ApplicationInputOutcomeUnknown`, canonical `AgentEvent` values, and
bounded typed presentation/artifact facts. No raw native envelope crosses the
Application boundary.

The current formal export is `CodexApplicationAdapter` from the lazy
`imagent.applications` facade, implemented in `src/imagent/applications/appserver.py`.
The target exact export is
`imagent.applications.adapters.codex:CodexApplicationAdapter`; the top facade
must preserve object identity and lazy cold-import behavior.

## Dependencies, state, and recovery

Codex depends on Interaction messages/operations/media, common Applications
contract/capabilities/events/operations/requests, four direct App Server
leaves (client, mapping, requests, and diagnostics), and the two presentation
leaves. Transport remains behind the App Server client rather than becoming a
direct adapter dependency. A candidate active-turn read never
authorizes fallback start. The typed pre-dispatch fence runs exactly once;
after native dispatch, missing acceptance is unknown. Native history is the
recovery authority. App Server queue reset or presentation failure becomes an
explicit observation gap, not silent continuation or a second subscriber.

## Current, target, and structural gap

Current code is the shared `src/imagent/applications/appserver.py`; it contains
the common App Server base plus `ZenApplicationAdapter`, so Codex and Zen are
declared split candidates rather than duplicate implementations. Current
evidence is in `tests/test_appserver_client.py`,
`test_appserver_input.py`, `test_appserver_mapping.py`,
`test_appserver_requests.py`, and the two Applications presentation suites.
The target is `src/imagent/applications/adapters/codex.py` with focused tests
at `tests/applications/adapters/test_codex.py`. The gap is the mechanical
adapter split while preserving native notification ordering, request/runtime
ownership, and artifact/live state machines.

## Authority

- [Adapter block](../README.md)
- [Codex transition page](../../../application-adapters/adapters/codex.md)
- [Applications adapter overview](../../../application-adapters/design.md)
- [ADR 0003](../../../../decisions/0003-attachment-sources-and-trust.md)
- [ADR 0008](../../../../decisions/0008-interactive-request-routing.md)
- [ADR 0012](../../../../decisions/0012-input-continuation-and-reply-correlation.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

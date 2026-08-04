# App Server diagnostics design

Component ID: `applications.adapters.appserver.diagnostics`

Parent: `applications.adapters.appserver`

## Purpose and ownership

This leaf owns the App Server-specific diagnostic provider: fixed redacted
connection/queue facts, process-local overflow/failure counters, and the
legacy debug summary/logging helpers used inside the adapter. It reports the
App Server connection epoch and independent dispatch lane state without
becoming an operator or Gateway health policy.

It does not own common diagnostic enums/value definitions, Gateway
diagnostics, polling, retry decisions, or consumer presentation. The
historical aggregate
`src/imagent/diagnostics.py` remains a shared contract location until a later
focused ownership split; only its App Server-specific positions belong here.

## Typed boundary and public facade

Inputs are local transport/runtime transitions and already received native
messages. `AppServerDiagnosticState.snapshot()` produces the ADR-0014
read-only, bounded, redacted `ConnectionDiagnosticFacts` surface. Separate
legacy debug summaries are internal mappings and logs; they are not part of
that redaction guarantee and currently may include native/response IDs,
content or error previews, commands, cwd, questions, and changed paths.
Snapshot reads perform no I/O.

The current formal export is `AppServerDiagnosticState` from
`imagent.applications.appserver_client.diagnostic_facts`. The target exact
owner is `imagent.applications.adapters.appserver.diagnostics`; the
historical client facade identity remains stable during migration.

## Dependencies, state, and recovery

The provider depends on the Applications diagnostic contract and internal App
Server mapping classification. Counters and the current epoch are bounded
process-local state. Notification/server-request overflow is explicit and
connection-scoped; reset recovery is owned by the client/adapter, not by a
diagnostic exporter. ADR-0014 redaction applies to the fixed diagnostic facts.
The legacy debug-summary/logging path still needs a focused security pass that
bounds previews and defines which native IDs, paths, commands, questions, and
content-derived values may be retained or emitted.

## Current, target, and structural gap

Current code is split across the App Server diagnostic fact, summary, and
runtime files plus the App Server-specific portion of
`src/imagent/diagnostics.py`:

- `src/imagent/applications/appserver_client/diagnostic_facts.py`
- `src/imagent/applications/appserver_client/diagnostics.py`
- `src/imagent/applications/appserver_client/runtime_diagnostics.py`
- `src/imagent/diagnostics.py`

Current evidence is `tests/test_appserver_client.py`,
`tests/test_appserver_transport.py`, and `tests/test_diagnostics.py`; the target suite is
`tests/applications/adapters/appserver/test_diagnostics.py`. The gap is to
move only App Server-specific implementation while preserving the common
diagnostic contract and keeping Applications independent of Gateway, plus the
legacy debug-summary redaction/bounding work described above.

## Authority

- [App Server block](../README.md)
- [Diagnostics design](../../../../diagnostics/design.md)
- [Applications adapter overview](../../../../application-adapters/design.md)
- [ADR 0014](../../../../../decisions/0014-read-only-diagnostics-surface.md)

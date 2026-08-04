# Application live-activity presentation design

Component ID: `applications.presentation.live-activity`

Parent: `applications.presentation`

## Purpose and ownership

This A1 leaf offers adapter-specific bounded non-artifact facts to a concrete
presentation protocol. It owns `CodexLiveActivityPresenter`,
`T3ActivityPresenter`, their distinct frozen fact types, finite text output,
the bounded invocation runtime, and redacted presentation diagnostics.

It does not own a destination policy, attachment materialization, raw native
payload/client, a generic Application hook, a second observer, or a Gateway
checkpoint. Codex and T3 facts remain separate types because their native
evidence and recovery semantics differ.

## Inputs, outputs, dependencies, and exports

Codex passes `CodexLiveActivityFacts`; T3 passes `T3ActivityFacts`. Each fact
already has fixed stable identity, native kind, scalar bounds, and no raw
protocol envelope. A presenter returns `ApplicationTextPresentation` or
`None`: finite typed text only, with no power to change item/event identity,
Thread, Turn, role, metadata, attachments, destination, or recoverability.

The leaf depends on Interaction messages and Application contract/event values.
Current exports are from `imagent.applications`; target exports are from
`imagent.applications.presentation`, implemented at
`src/imagent/applications/presentation/live_activity.py`.

## State and recovery

Invocation is async but capacity, lifetime, cancellation, cancellation-overrun
tracking, seen-identity windows, and diagnostics are finite. It runs in the
adapter's existing ordered normalization lane and never on an Application
socket read path. Failure is a fixed adapter-owned recovery gap/diagnostic;
facts, rendered output, identity, and exception text are not persisted.

Codex activity is live-only: it emits `message.created`, may be lost across a
gap/reconnect, and never advances a completion checkpoint. T3 activity is
recoverable only because polling, catch-up, and authoritative history reproduce
the same stable association; replay-safe presenters may be reinvoked after a
finite process-local dedupe window expires. An absent presenter preserves
native adapter behavior exactly.

## Current and target structure

Current implementation is `src/imagent/applications/presentation.py` with
diagnostic fact types in `src/imagent/diagnostics.py`. Current tests are
`tests/test_application_presentation.py`, `tests/test_appserver_mapping.py`,
and `tests/test_t3_client.py`; target tests are
`tests/applications/presentation/test_live_activity.py`. The explicit gap is
that Codex/T3 fact shapes share a runtime while remaining irreducibly separate
typed positions.

## Authority

- [Architecture](../../../../ARCHITECTURE.md)
- [Application adapter design](../../../application-adapters/design.md)
- [ADR 0015](../../../../decisions/0015-typed-extension-seams-and-composition.md)

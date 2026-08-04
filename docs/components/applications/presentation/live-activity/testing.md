# Application live-activity presentation testing

Before this slice, evidence was `tests/test_application_presentation.py`,
`tests/applications/adapters/appserver/test_mapping.py`, and `tests/test_t3_client.py`. Focused
evidence now lives at `tests/applications/presentation/test_live_activity.py`.

Tests prove bounded immutable Codex and T3 facts, finite text-only output,
distinct fact/identity domains, timeout/cancellation/overrun/capacity behavior,
and redacted bounded diagnostics. Invocation must use the existing ordered
adapter path, never a socket read task or second subscription.

Codex cases prove stable native event identity where available, ordered
`message.created`, finite live dedupe, no authoritative-history replay, and no
completion-checkpoint advance. T3 cases prove identical stable association in
polling, catch-up, and history; a replay-safe presenter may be invoked again,
while polling failure produces an explicit recoverable gap without erasing
accepted input correlation. Both adapters prove that absence changes nothing.

The focused owner suite also proves that the package facade re-exports the
exact live-activity objects and that the old monolithic module is absent.
Artifact-materialization facade and historical-module absence evidence lives
in its sibling focused owner suite.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.applications.presentation.test_live_activity \
  tests.applications.adapters.appserver.test_mapping tests.test_t3_client -v
```

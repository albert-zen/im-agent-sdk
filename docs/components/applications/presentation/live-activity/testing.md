# Application live-activity presentation testing

Before this slice, evidence was `tests/test_application_presentation.py`,
`tests/applications/adapters/appserver/test_mapping.py`, and the former T3
client test. Focused
evidence now lives at `tests/applications/presentation/test_live_activity.py`.

Tests prove bounded immutable Codex and T3 facts, finite text-only output,
distinct fact/identity domains, timeout/cancellation/overrun/capacity behavior,
and redacted bounded diagnostics. Invocation must use the existing ordered
adapter path, never a socket read task or second subscription.

Codex cases prove required stable native event identity before a configured
presenter can run, ordered `message.created`, finite live dedupe, no
authoritative-history replay, and no completion-checkpoint advance. A missing
native event ID creates the fixed mapping gap rather than a UUID/text/time/raw
payload substitute; the absent presenter preserves prior invisibility. T3
cases prove identical stable association in polling, catch-up, and history; a
replay-safe presenter may be reinvoked after a finite process-local dedupe
window expires, while polling failure produces an explicit recoverable gap
without erasing accepted input correlation. Both adapters prove that absence
changes nothing.

The focused owner suite also proves that the package facade re-exports the
exact live-activity objects and that the old monolithic module is absent.
Artifact-materialization facade and historical-module absence evidence lives
in its sibling focused owner suite.

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.applications.presentation.test_live_activity \
  tests.applications.adapters.appserver.test_mapping \
  tests.applications.adapters.test_t3 -v
```

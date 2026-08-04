# Application live-activity presentation testing

Current evidence is `tests/test_application_presentation.py`,
`tests/test_appserver_mapping.py`, and `tests/test_t3_client.py`; target
focused evidence is `tests/applications/presentation/test_live_activity.py`.

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

```sh
PYTHONPATH=src uv run python -m unittest \
  tests.test_application_presentation tests.test_appserver_mapping tests.test_t3_client -v
```

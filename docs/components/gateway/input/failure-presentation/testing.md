# Gateway inbound failure presentation testing

Focused evidence is `tests/gateway/input/test_failure_presentation.py`, with
durable restart evidence in `tests/test_storage.py`; persistence integration
stays with the SQLite owner.

Tests must prove:

- exact phase classification for I1/Controller/pre-dispatch failure, unknown
  native outcome, and post-acceptance failure;
- configured pre-acceptance completion precedes rendering/delivery, while
  absence retains release-and-raise;
- unknown remains `side_effect_started` and post-acceptance remains terminal
  across timeout, cancellation, invalid output, presenter failure, Channel
  failure, duplicate delivery, and restart;
- original pre-fence cancellation releases without presentation and a
  post-fence race never reopens the claim;
- presenter facts/output contain only fixed bounded typed identity, reject
  attachments/metadata/identity changes, and retain no exception or content;
- active task capacity, lifetime, shutdown cleanup, and diagnostics are finite;
- output uses common delivery idempotency and never creates a second admission,
  subscription, transcript, spool, outbox, or runtime.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.input.test_failure_presentation tests.test_storage -v
```

# App Server artifact materialization testing

Current coverage is `tests/test_appserver_artifacts.py`; target focused
coverage is `tests/applications/presentation/test_artifact_materialization.py`.

Tests prove that facts contain only finite typed candidates; locators are
untrusted; missing Thread/Turn/item IDs fail before consumer work; and distinct
items sharing a locator keep distinct native identities. Materializers may
return only bounded typed attachments and the adapter associates them in native
item order, with at most one artifact-only terminal fallback.

Live duplicate suppression is finite, history reinvocation is replay-safe, and
the materializer never receives bytes, a trusted path, raw payload, client,
credential, route, checkpoint, or delivery authority. Capacity, timeout,
cancellation/overrun, malformed output, consumer failure, reconnect/gap, and
shutdown must all be explicit and bounded. Failure terminates the affected
live Thread without leaking later notifications; absent materialization leaves
Codex and Zen behavior unchanged.

```sh
PYTHONPATH=src uv run python -m unittest tests.test_appserver_artifacts -v
```

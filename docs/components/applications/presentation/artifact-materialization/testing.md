# App Server artifact materialization testing

Focused coverage is
`tests/applications/presentation/test_artifact_materialization.py`; the
`tests/applications/` and `tests/applications/presentation/` package markers
remain in place so the full unittest discovery tree includes it.

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
PYTHONPATH=src uv run python -m unittest \
  tests.applications.presentation.test_artifact_materialization -v
```

The focused suite also proves that every artifact contract has one identity
through `imagent.applications`, `imagent.applications.presentation`, and the
owner module; the historical `imagent.applications.appserver_artifacts`
module cannot be found or imported; a clean subprocess can import the nested
presentation facade without loading adapters; and `typing.get_type_hints`
resolves the owner protocol/runtime annotations without a historical-module
reference. The built base wheel repeats the facade identity and old-module
absence checks.

The public reference suite adds consumer-owned partial-file startup recovery,
cancellation/failure release, per-destination completion cleanup, restart
sweep, root confinement, capacity, and SDK database/sidecar non-persistence.

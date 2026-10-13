# Application capabilities testing

Focused ownership coverage lives in `tests/applications/test_capabilities.py`;
`tests/conformance/test_adapter_contracts.py` retains the cross-adapter conformance
evidence.

Tests validate the exact aggregate and nested record types, every
enum/discriminant field, project mode and Thread deletion declaration, the
attachment collection and every attachment-source value,
replay/order/request/runtime claims,
rejection of inconsistent capability combinations, and that importing the
capability owner does not initialize concrete adapters or Gateway modules.
Clean-process import-order cases prove the owner is the sole capability public
surface: the package root and `imagent.contracts` do not expose these names.
Adapter conformance
must prove a capability maps to real native behavior or an explicit
unsupported result—never a hidden fallback or product policy.

Managed discovery/reading must remain native when advertised. Fixed/flat
discovery and reading must expose exactly one stable adapter workspace Project;
their creation/deletion support must remain unsupported, and capability
preflight must agree with the runtime typed result.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.test_capabilities -v
PYTHONPATH=src uv run python -m unittest tests.conformance.test_adapter_contracts -v
uv run python scripts/validate_schemas.py
```

Any capability change also requires concrete adapter evidence from the
applicable integrations and the repository-wide validation gates.

`tests/gateway/test_creation_profiles.py` exercises public Gateway actions with
both concrete Zen and Codex adapters: configuration snapshot, concurrent
Conversation selection, default preservation, same-action replay, changed-profile
conflict, explicit unsupported selection, Python/schema bounds, and same-ID
changed-configuration conflict after SQLite restart without duplicate creation.
Primitive and workflow unknown-profile rejections replay durably, and registering
the profile after restart cannot create or bind under the same action ID. Changed
unknown selections conflict; a new action with the now-registered profile works.
Schema checks cover the creation-support dependency; ID uniqueness remains a
required semantic validation rule.

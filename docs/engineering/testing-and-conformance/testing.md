# Testing and conformance testing

## Focused checks

Run the reusable kit directly while developing it:

```sh
PYTHONPATH=src uv run python -m unittest tests.conformance.test_adapter_contracts -v
uv run python scripts/validate_schemas.py
```

The focused suite also checks the package boundary: the
`imagent.interaction.testing` owner and the finite `imagent.testing`
compatibility facade export the same objects in either import order, while the
historical `imagent.testing.contracts` and `imagent.testing.fakes` modules do
not exist. This proves the move did not leave a duplicate implementation or a
second public module tree.

The full repository gate remains the acceptance check for a conformance
change:

```sh
PYTHONPATH=src uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests scripts
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src tests scripts
```

## Evidence matrix

Every adapter contract change should cover the applicable rows:

| Evidence | Required proof |
|---|---|
| Identity | Stable resource, message, event, operation, and delivery identities survive the asserted retry or recovery window. |
| Lifecycle | Start, stop, cancellation, and cleanup are bounded and do not leak a task or subscription. |
| Capability | Supported behavior works; unsupported or unavailable behavior is explicit and is not silently approximated. |
| Ordering | Only ordering promised by the native producer is asserted; a gap or expired cursor is visible. |
| Recovery | Reconciliation uses authoritative Application history/catch-up and never a synthetic transcript. |
| Capacity | Each fake queue, task lane, and fixture collection has a finite limit and explicit overflow behavior. |
| Absence | The adapter or extension-free path keeps the prior behavior and does not require an optional extension. |

The shipped-adapter ledger runs this reusable surface for QQ, Telegram,
Feishu, and Weixin with disabled, side-effect-free native configuration, and
for Codex, Zen, and T3 with deterministic native clients. Adapter-owned suites
then supply the native input/event/history/request/media/diagnostic and
cancellation evidence that cannot honestly be manufactured by the common kit.

## Adding an adapter or common assertion

1. Document the native source of truth and capability limits in its runtime
   component page.
2. Run the common kit without false fallback claims.
3. Add focused native mapping, lifecycle, and recovery tests.
4. Add a second-integration proof before generalizing a new semantic.
5. If the change is an ADR 0015 seam, test the exact stage, bounded identity,
   failure/idempotency rule, and one valid counterexample with the extension
   absent.

Replay tests must distinguish I1 pre-dispatch re-entry, A1 authoritative
recovery, O1 idempotent suppression, and non-durable O2 observation. A passing
fake alone is not evidence that a native adapter can recover the behavior.

The reusable kit itself must remain independent of product packages. Its
fixtures may use only public contracts and bounded state; they must not import
Gateway orchestration or a concrete Application implementation to manufacture
truth. The moved test lives at `tests/conformance/test_adapter_contracts.py`;
its native adapter cases continue to provide the existing cross-integration
evidence without changing the contract kit's admission rules.

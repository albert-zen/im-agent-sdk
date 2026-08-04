# Contracts testing

Focused Interaction scenarios are now owned by the
[messages](../interaction/messages/testing.md) and
[operations](../interaction/operations/testing.md) test designs, with source
and trust coverage owned by [media](../interaction/media/testing.md). This broad
page remains current evidence for contract leaves not yet mechanically split.

## Required checks

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_contracts -v
python scripts/validate_schemas.py
ruff check src/imagent/contracts tests/test_contracts.py scripts
pyright src/imagent/contracts tests/test_contracts.py scripts
```

## Contract scenarios

Coverage must preserve:

- managed, flat, and fixed project modes;
- reference scoping and cross-Application rejection;
- valid started/create-new and steered/preserve-existing input dispatch and
  accepted-Turn schema combinations, with mixed policies rejected;
- stable client message IDs;
- typed operation/result discriminant matching;
- explicit error codes and unsupported behavior;
- attachment source discrimination and capability gating;
- `eventId` requirements and honest optional ordering fields;
- binding and projection-route invariants;
- checkpoint pair validation and Turn reply-correlation identity;
- multiple completed messages inside one Turn.
- live completed-event and authoritative-history projection preserve bounded
  namespaced Agent Metadata without changing delivery/checkpoint semantics.

Any schema change requires a matching Python model/validator change and the
reverse. Concrete Codex, Zen, T3, IMCodex, and fake adapters must still satisfy
the affected contract. Applications facade tests additionally prove exact
owner identity, clean-process failure for retired `imagent.contracts` names,
and import-order independence without relying on module cache.

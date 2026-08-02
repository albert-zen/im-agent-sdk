# Contracts testing

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
- created versus completed message projection retains its checkpoint
  distinction without assigning common meaning to adapter Metadata.

Any schema change requires a matching Python model/validator change and the
reverse. Concrete Codex, Zen, T3, IMCodex, and fake adapters must still satisfy
the affected contract.

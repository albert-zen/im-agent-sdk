# Channel contract testing

Required evidence:

- stable configured Channel identity and immutable truthful capabilities;
- modern three-callback lifecycle plus explicit legacy two-callback migration
  behavior without retrying a partially started adapter;
- one-shot admission lease identity, ownership transfer, fenced release, and
  mismatch/duplicate rejection;
- send/receipt validation for accepted, rejected, retryable, partial, and
  unknown outcomes without invented native identity;
- startup validation is optional, synchronous, repeatable, side-effect-free,
  and uses the same resolved configuration as start;
- diagnostic capability absence/failure cannot break lifecycle or inject
  provider identity;
- fakes and all native adapters satisfy the structural contract; and
- Interaction contract code imports no Gateway or concrete adapter.

Focused evidence currently lives in `tests/test_adapter_contracts.py`,
`tests/test_native_channels.py`, Channel-specific suites, Gateway admission
tests, schema validation, and Pyright. Receipt identity and validation evidence
lives in `tests/interaction/channels/test_contract.py`; lifecycle, capability,
and admission evidence remains in the historical suites until its own focused
mechanical slice.

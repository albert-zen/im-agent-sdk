# Channel contract testing

Required evidence:

- stable configured Channel identity and immutable truthful capabilities;
- Channel capability fields use `DeliverySupportLevel`, while Application
  capabilities continue using the distinct `SupportLevel` type;
- v1 capability field/positional order, string discriminants, and derived
  `DeliveryProfile` remain stable through the API transition;
- modern message-plus-admission lifecycle plus explicit legacy message-only
  migration behavior without retrying a partially started adapter;
- one-shot admission lease identity, ownership transfer, fenced release, and
  mismatch/duplicate rejection;
- send/receipt validation for accepted, rejected, retryable, partial, and
  unknown outcomes without invented native identity;
- startup validation is optional, synchronous, repeatable, side-effect-free,
  and uses the same resolved configuration as start;
- diagnostic capability absence/failure cannot break lifecycle or inject
  provider identity;
- fakes and all native adapters satisfy the structural contract; and
- Channel contract/runtime/fakes import no `GatewayOperation`, operation
  handler, Gateway implementation, or concrete adapter.
- `imagent.interaction.channels.ChannelAdapter` owns the Protocol, while
  `imagent.adapters.ChannelAdapter` preserves exact object identity without a
  parallel definition.

Focused evidence currently lives in `tests/conformance/test_adapter_contracts.py`,
`tests/test_native_channels.py`, Channel-specific suites, Gateway admission
tests, schema validation, and Pyright. Exact ownership identity for admission,
startup validation, capability, and receipt contracts lives in
`tests/interaction/channels/test_contract.py`; native Channel suites and
planner tests provide behavioral parity. Gateway operation tests enter through
`ControllerActions` or the public typed Gateway execution surface rather than
injecting an operation through a fake Channel lifecycle callback.

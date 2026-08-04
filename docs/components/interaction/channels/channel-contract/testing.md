# Channel contract testing

Required evidence:

- stable configured Channel identity and immutable truthful capabilities;
- Channel capability fields use `DeliverySupportLevel`, while Application
  capabilities continue using the distinct `SupportLevel` type;
- v1 capability field/positional order, string discriminants, and derived
  `DeliveryProfile` remain stable through the API transition;
- direct standalone Channel use may omit admission, while Gateway startup
  passes the exact message and admission handlers once to every SDK-owned
  Channel and has no legacy message-only compatibility path;
- a one-argument Channel fails Gateway call binding before its body, and a
  `TypeError` from a valid two-argument start body remains one real startup
  failure rather than triggering another invocation;
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
- `imagent.interaction.channels` is the sole formal Channel contract facade;
  its exported values are exact owner objects and its `__all__` contains no
  historical facade implementation;
- the canonical `interaction.channels.contract` owner has the same finite
  export set, and clean-process `inspect.signature`/`typing.get_type_hints`
  resolve through the owner without importing a historical implementation;
- the enumerated Channel/admission names are absent and unimportable from
  `imagent.adapters`, and the capability/receipt names are absent and
  unimportable from `imagent.contracts`, without disturbing unrelated facade
  names; and
- the separate `imagent.channels` adapter facade preserves exact identity for
  `NativeTransportChannelAdapter` and `channel_from_config`.

Focused evidence currently lives in `tests/conformance/test_adapter_contracts.py`,
`tests/interaction/channels/adapters/test_native_channels.py`, Channel-specific
suites, Gateway admission tests, schema validation, and Pyright. Exact sole-facade ownership, retired
historical imports, and `imagent.channels` adapter identity live in
`tests/interaction/channels/test_contract.py`; native Channel suites and
planner tests provide behavioral parity. Gateway operation tests enter through
`ControllerActions` or the public typed Gateway execution surface rather than
injecting an operation through a fake Channel lifecycle callback.

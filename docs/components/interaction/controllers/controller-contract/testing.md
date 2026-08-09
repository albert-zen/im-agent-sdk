# Controller contract testing

Required evidence:

- handler and registry annotations name the exact Gateway-owned
  `ConversationActions` type;
- the same object identity reaches a product handler;
- `ControllerActions` and `CommandHandlerActions` are absent from owners,
  facades, root exports, and clean-wheel imports;
- `None` continues ordinary input; any finite tuple consumes it;
- output for another Conversation fails before delivery;
- effectful invocation enters the one-way owned inbound fence before the
  handler, while read-only invocation does not;
- fence failure prevents handler invocation and cancellation never creates
  retry authority;
- lifecycle validation rejects an unfrozen registry and close boundedly joins
  registry work.

Focused owner tests are
`tests/interaction/controllers/test_contract.py` and
`tests/interaction/controllers/test_registry.py`.

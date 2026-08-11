# Gateway diagnostics testing

The focused Gateway evidence lives in
`tests/gateway/test_diagnostics.py`. Lower-layer contract evidence remains in
`tests/interaction/test_diagnostics.py`,
`tests/interaction/channels/test_diagnostics.py`, and
`tests/applications/test_diagnostics.py`; native adapter state stays covered
by its adapter-owned tests.

Required Gateway coverage:

- immutable, schema-versioned, non-authoritative snapshots with unchanged
  constructor order, absence behavior, validation messages, and queue bounds;
- projection/recovery aggregation with fixed gap codes and no native IDs,
  exception text, or consumer-controlled strings in serialized facts;
- hostile, raising, infinite, and over-bound projection providers with the
  exact 4,096-record and 1,000,000-counter saturation bounds;
- startup admission and I1/I2/O1/O2 facts with fixed failure vocabularies,
  bounded counters, cancellation-overrun/capacity relationships, and absent
  optional providers remaining absent;
- application and Channel provider collection that preserves configured
  identity, sorts deterministically, validates queue scope, and fails closed
  without leaking provider/native values;
- T3's absent long-lived connection and existing Application/Channel queue
  scope distinctions;
- direct Gateway call-site imports from `imagent.gateway.diagnostics`, with
  no runtime dependency from the Gateway owner back to `imagent.diagnostics`;
- exact `__all__` ownership, no definitions or lazy resolver in the transition
  facade, exact object identity through `imagent.gateway` and
  `imagent.diagnostics`, and both canonical-first and facade-first imports;
- `typing.get_type_hints`/signature evidence for Gateway snapshot and
  collection boundaries resolving to the canonical owner objects;
- clean-wheel import-order and identity smoke coverage for the canonical
  Gateway owner plus the existing Interaction, Channel, Application, and
  adapter assertions.

Run focused diagnostics tests first, then the full repository gates, wheel
build, and all clean-install smoke cases. Review guidance is run once for the
completed slice before the AgentKit lifecycle is closed.

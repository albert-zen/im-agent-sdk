# Interaction diagnostics contract testing

Focused evidence lives in `tests/interaction/test_diagnostics.py`.

The suite proves:

- the five canonical objects are defined by
  `imagent.interaction.diagnostics` and preserve their exact frozen/slot
  shapes, signatures, fixed vocabularies, bounds, and validation behavior;
- values remain redacted, bounded, immutable, and free of I/O, callbacks,
  aggregation, or native payloads;
- `imagent.diagnostics` re-exports the exact moved objects by identity without
  a duplicate definition or lazy attribute resolver;
- an AST/import subprocess check proves the canonical Interaction module
  imports neither `imagent.applications` nor `imagent.gateway`; and
- the dependency-neutral owner remains importable from a base installation.

Channel-scoped facts and provider identity are covered by the focused
`tests/interaction/channels/test_diagnostics.py` suite. Native lifecycle and
queue behavior remains covered by
`tests/interaction/channels/adapters/test_diagnostics_owner.py` and
`tests/interaction/channels/adapters/test_native_channels.py`.

Run the focused tests together with the Applications, Gateway, and native
Channel suites. The full repository suite is required because the transition
facade still retains Gateway definitions while #273 is pending.

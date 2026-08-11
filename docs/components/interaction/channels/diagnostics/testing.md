# Interaction Channel diagnostics contract testing

Focused evidence lives in `tests/interaction/channels/test_diagnostics.py`.

The suite proves:

- `ChannelDiagnosticFacts` and `ChannelDiagnosticsProvider` are defined only
  by `imagent.interaction.channels.diagnostics`;
- Channel identity/kind is preserved, connection queue scope rejects
  Application/Gateway queue names, over-512-character identities and
  non-exact nested facts fail before serialization, and the value remains
  frozen and bounded;
- `imagent.diagnostics` exposes the exact same objects by identity without a
  duplicate class or lazy `__getattr__`;
- the canonical Channel module imports only Interaction and standard-library
  modules, never Applications or Gateway; and
- native adapter diagnostics remain a distinct owner while importing the
  canonical common contracts.

The existing native adapter ownership tests retain lifecycle, reconnect,
overflow, and clean-process historical-path evidence. No test adds socket
I/O, callbacks, aggregation, a second subscription, or a new admission path.

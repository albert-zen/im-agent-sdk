# Interaction Channel diagnostics contracts design

## Responsibility

This leaf owns the Channel-scoped immutable diagnostic fact and optional
provider protocol. Its sole implementation owner is
`src/imagent/interaction/channels/diagnostics.py`:

- `ChannelDiagnosticFacts` preserves configured Channel identity/kind and an
  optional bounded `ConnectionDiagnosticFacts` whose queue scope is only
  `channel_inbound`;
- `ChannelDiagnosticsProvider` is the optional structural
  `diagnostic_facts()` capability; absence, invalid output, provider failure,
  or identity mismatch remains an honest identity-only result at the
  collecting runtime boundary.

The module imports only Interaction values, specifically the dependency-neutral
diagnostic contracts from `imagent.interaction.diagnostics`, plus standard
library typing/dataclass machinery. It imports neither Applications, Gateway,
native SDKs, persistence, socket code, callbacks, or a service locator.

## Boundary with native adapters

`src/imagent/interaction/channels/adapters/diagnostics.py` is a different
owner. It retains native queue/connection snapshots, bounded lifecycle state,
and native health/debug helpers. Those helpers import and consume the new
canonical Interaction contracts where they cross the adapter boundary; they do
not redefine `ChannelDiagnosticFacts`, `ChannelDiagnosticsProvider`, or the
shared connection/queue vocabulary. Native reads remain synchronous,
side-effect-free, bounded, and free of consumer work on a provider socket
callback.

The `imagent.diagnostics` transition facade re-exports the exact Channel
objects from this leaf while Application/Gateway diagnostics finish their own
focused migrations. This is an explicit compatibility surface, not a second
implementation.

ADR 0014 governs redaction, absence, provider failure, fixed failure codes,
and bounded queue semantics. ADR 0015 does not turn this capability into a
generic hook.

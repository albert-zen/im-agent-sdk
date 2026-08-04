# Diagnostics testing

The canonical common contract tests now live in
`tests/interaction/test_diagnostics.py`, and Channel contract tests live in
`tests/interaction/channels/test_diagnostics.py`. This page remains the
transition-facade and remaining Application/Gateway aggregation evidence;
those tests also verify exact object identity through `imagent.diagnostics`
while #276/#273 are pending.

Required coverage:

- immutable/versioned/non-authoritative snapshot shape;
- recursive serialization contains no native Thread IDs or free-form error
  and gap text;
- unknown gap values collapse to the bounded `other` classification;
- projection running/retrying/degraded, overflow, delivery failure, request
  recovery, and gap aggregates;
- Gateway startup queue capacity/depth/overflow facts without changing
  admission behavior;
- configured I1 invocation/success/failure/timeout/cancellation and bounded
  cancellation-overrun counters plus fixed last-failure codes without
  identities, content, paths, return values, or exception text; active task
  capacity and its rejection counter are finite, and an absent transformer
  exposes no fabricated I1 facts;
- configured I2 exposes only its fixed execution/cancellation/capacity facts;
  no exception text, origin/reply/delivery identity, or rendered output is
  retained, and an absent presenter exposes no fabricated I2 facts;
- configured A1 exposes only fixed execution/omission/cancellation/capacity
  facts on its owning Application adapter; native facts, IDs, rendered output,
  and exception text are absent, and default adapters fabricate no A1 facts;
- configured App Server artifact A1 exposes only its distinct fixed execution,
  live-duplicate, cancellation, capacity, and failure facts; candidates,
  locators, paths, identities, attachments, consumer state, and exception text
  are absent, and an absent materializer fabricates no facts;
- configured O1 exposes only fixed invocation/delivery/suppression/failure,
  timeout, cancellation-overrun, and capacity facts; message, destination,
  policy output, and exception text are absent, and an absent policy exposes no
  fabricated O1 facts;
- configured O2 exposes only fixed notification/success/failure/timeout,
  cancellation-overrun, and capacity facts; logical content, identities,
  receipts, errors, observer output, and exception text are absent, and an
  absent observer exposes no fabricated O2 facts;
- App Server ready/reconnect epoch and queue overflow transitions for both
  notification and server-request lanes;
- queue depth never exceeds configured capacity in a returned snapshot;
- T3 returns no fabricated long-lived connection;
- QQ/Telegram/Feishu/Weixin lifecycle facts retain configured identity and
  contain no native/provider labels;
- QQ/Feishu `channel_inbound` depth/capacity/overflow are bounded, while
  Telegram/Weixin expose no synthetic queue;
- Channels without the optional provider, providers that raise, return an
  invalid shape, or mismatch configured identity still appear safely;
- repeated reads do not mutate state and no exporter or callback is required.
- `imagent.diagnostics` re-exports the canonical Interaction common and Channel
  objects by identity without duplicate definitions or lazy `__getattr__`.

The native Channel implementation owner also has a clean-process structural
test proving the historical `imagent.channels.native.diagnostics` module no
longer exists; this does not create a second public diagnostics facade.

Run the full suite after changing the public facts because Gateway,
projection, Codex/Zen, and T3 composition are all involved.

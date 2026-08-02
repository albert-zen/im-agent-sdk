# Diagnostics testing

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

Run the full suite after changing the public facts because Gateway,
projection, Codex/Zen, and T3 composition are all involved.

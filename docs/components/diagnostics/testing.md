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

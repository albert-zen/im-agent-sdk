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
- Channel providers preserve configured identity/kind and native lifecycle
  transitions without leaking provider fields;
- adapters without the optional provider, providers that raise, and providers
  returning mismatched identity still appear safely;
- repeated reads do not mutate state and no exporter or callback is required.

Run the full suite after changing the public facts because Gateway,
projection, Codex/Zen, and T3 composition are all involved.

# Gateway store testing

The memory and SQLite implementations run the same conformance suite. It must
prove:

- exact public `GatewayStore`, `MemoryGatewayStore`, and
  `SQLiteGatewayStore` identities and no public repository bundle;
- one namespace, exclusive acquisition, store-authored expiry, renewal,
  monotonic epochs, release, crash takeover, and stale owner/token/epoch/
  expired-current-lease rejection for every mutation family, plus parity when
  same-owner reacquisition renews the shared lease past an older session's
  copied expiry;
- unchanged reads cannot grant mutation authority after lease loss;
- fixed/flat workspace fingerprint parity and changed-root startup rejection
  without persisted root paths;
- monotonic binding generations across select, same-target select, every
  clear, delete/recreation, restart, compaction-shaped row removal, and ABA;
- thread/project/application clears derive retained ancestors in-transaction,
  always advance generation including while unbound, and replay an older
  terminal clear after a newer selection without changing that selection;
- atomic capacity reservation, fingerprint, binding/route/generation, and
  terminal store-action receipt commits, including rollback and lost
  acknowledgement replay before current-state inspection;
- one shared exact receipt bound across store, native, request-response, and
  workflow categories, with no eviction or time-based retry permission;
- `native_side_effect_started` durability before callbacks, sticky unknown
  after crash/cancellation/lost acknowledgement, and no blind repeat;
- reconciliation only through an explicitly supplied evidenced idempotency or
  terminal-status port, with negative lookup/temporary `not_found` remaining
  unknown;
- create/select and create/bind binding-generation CAS, atomic foreground
  route plus binding plus terminal phase, known-result resume, stale partial,
  and no compensation;
- a paused old owner fenced before native work, takeover after the native
  fence, and stale-old-owner rejection when it resumes;
- SQLite restart returns terminal old-action results without reapplying them
  over later bindings; and
- schema/value inspection contains only bounded identity, digest, generation,
  lease, phase, fixed error, and minimal stable-reference fields;
- legacy binding `revision` is renamed to `generation`, while legacy delivery
  error/detail text is securely scrubbed from the database and sidecars before
  the current schema is admitted.

Cancellation tests cover every await boundary before reservation, after
reservation but before the native fence, after the native fence, after a known
native result, and after the atomic terminal commit. Only the actual durable
phase determines the returned outcome.

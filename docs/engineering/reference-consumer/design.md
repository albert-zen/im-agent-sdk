# Reference consumer design

## Purpose

The reference consumer is the repository's one executable acceptance consumer
for the public v1 SDK. It continuously proves that a clean-installed downstream
composition can create explicit managed resources, bind and switch two IM
Conversations, send ordinary input, observe authoritative output once, inspect
bounded diagnostics, reconstruct bridge projections from SQLite with fresh
runtime objects, and shut down without owned work remaining.

It is acceptance code, not a second Gateway, Agent runtime, transcript,
repository implementation, product starter, or test-only bypass. Runtime
semantics remain authoritative in `docs/components/`; the complete accepted
scenario remains normative in `docs/V1_EXECUTABLE_SPEC.md`.

## Ownership and boundary

This leaf owns:

- the four product-neutral modules under `examples/reference_consumer/`;
- the deterministic executable scenario and its bounded summary;
- the focused integration test at `tests/gateway/test_reference_consumer.py`;
- reference-consumer onboarding under `docs/onboarding/`; and
- source-tree and clean-wheel execution of the same entry point.

It does not own public SDK contracts, Gateway persistence or effect execution,
native adapter policy, product permissions, credentials, presentation policy,
artifact storage, downstream migration, or release publication.

The example imports only installed public SDK surfaces. It never imports a
private Gateway runtime, effect executor, adapter implementation, store session,
repository, claim, checkpoint, or test fixture. The local deterministic Channel
and managed Application implement public ports exactly as downstream adapters
do; their counters may be inspected by the focused test, but the scenario may
not call a fake resource mutation or native event emitter to make the flow pass.

## Canonical flow

The entry point constructs one explicit managed-CWD Application, one Channel,
one coherent `SQLiteGatewayStore`, one frozen local command registry, and one
`Gateway`. While the first Gateway context is running it uses only scoped public
actions and Channel ingress to perform the golden path. Conversation A first
discovers and reads the Application through both public action factories, then
creates its Project and Thread and completes a single-destination ordinary
round trip before Conversation B binds for shared fan-out. Non-command text
passes through the optional Controller and then follows D's single policy-free
ordinary-input resolver and dispatcher; the example supplies no alternate
dispatch hook. The full scenario is defined in
`docs/V1_EXECUTABLE_SPEC.md`.

After the first Gateway closes its Channel, registry work, lease, session, and
SQLite store, the example retains only the managed Application whose native
Project, Thread, and authoritative bounded history survive independently of the
SDK. Its explicit native ingress creates one completed output while the Gateway
is absent. A fresh Channel, registry, SQLite store object, and Gateway then open
the same database. Startup reconstructs both Conversation bindings, both active
per-destination routes and checkpoints, and terminal workflow receipts. One new
Thread subscription for the new process lifetime reconciles the missed output
to both destinations exactly once; it neither redelivers the prior checkpointed
output nor redispatches native input. Replaying the stable Project and Thread
workflow actions returns their original terminal results without another native
create call. Public binding generations match their pre-shutdown values, the
two destination checkpoints advance from their captured prior item to the one
missed item, and a duplicate prior Channel message identity is suppressed by
the reconstructed completed idempotency record without another Application
call or delivery.

The same public flow owns Block G acceptance without becoming a second runtime.
The Application emits an approval request through its native event stream to
two Conversations; only a delivered recipient may invoke the scoped response,
the Application's first resolution wins, terminal replay is idempotent, and an
authoritative pending snapshot preserves a second request across restart.
Focused public composition evidence separately proves that absent snapshot
support makes restored routing evidence stale before native work.

Proactive delivery injects a bounded principal-scoped authorizer and uses the
canonical Gateway's one Coordinator/store. A consumer-owned private artifact
ledger stages bounded bytes under a configured root, records leases outside the
SDK, fsyncs payload/ledger replacement, observes typed per-destination outcomes,
retains retryable leases through explicit retry, releases terminal/cancellation/failure
work, and sweeps recorded or partial crash leftovers under a finite
directory-entry bound at startup. Exact route
snapshots remain pinned across a later binding change; accepted/unknown partial
outcomes stay isolated and unknown is not resent on replay or SQLite restart.
Unsupported sources plus hostile root/digest/count/size/media/grouping facts
are rejected before the reference Channel's native-send counter. A live-only
projected item leaves completion checkpoints unchanged, while recoverable live
and history output share the same presentation signature.
Focused public runs also revoke and rotate credentials around Memory/SQLite
terminal replay, mutate attachment metadata after admission, and swap a rooted
pathname to an outside symlink after descriptor acquisition; none causes a
second or escaped native send.

Once the second composition closes, the executable opens the database read-only
and runs an integrity check over one consistent WAL-aware read transaction. A
complete current schema allowlist validates every `sqlite_schema` object key and
normalized table/index/trigger definition, column, declared SQLite type,
deterministic row count, runtime value type, and bounded JSON tree; unknown,
changed, view-bearing, or BLOB-bearing state fails closed. Bounded descriptor
reads cover the database plus every present WAL, SHM, or journal sidecar and
require stable entry names, regular-file kinds, device/inode identities, sizes,
and timestamps before and after inspection. They reject exact, UTF-16, base64,
hex, and common compressed forms of
unique transcript, native-payload, request-body, media, artifact, credential,
and workspace-path sentinels. Adversarial tests cover encoded BLOBs, fragmented
TEXT rows, WAL-only schema/value mutations, sidecar-only evidence, sidecar
appearance or disappearance, path replacement, non-file entries, and file growth during
inspection. This proves
the persisted surface remains bridge state plus rebuildable projections; it
does not make SQLite a transcript or Application-authority store.

Stable action, resource, message, event, and delivery identities drive every
assertion. Output isolation is proved by exact destination sets across shared
Thread, switch, and switch-back phases. At most one Application subscription is
active for each stable Thread in each Gateway lifetime. The printed result
contains only fixed labels, counts, and Booleans; it never emits content, CWDs,
Conversation/Thread IDs, credentials, endpoints, or exception text.

## Bounds and lifecycle

Every local collection and subscription has a positive finite bound. Capacity
failure occurs before mutation or append. The managed CWD is supplied
explicitly by the caller and is never a default or onboarding inference.

The Gateway async context owns startup and shutdown. The final scenario checks
that the Channel and Application are stopped, Application subscriptions are
closed, registry work is joined, the Gateway store lease/resources are closed,
and no scenario-owned task remains. Diagnostics are read synchronously while
running and validated only as bounded, redacted, non-authoritative facts.

## Packaging

The wheel includes `examples/reference_consumer/` so an isolated base install
can run `python -m examples.reference_consumer.main`. Packaging owns inclusion
and isolated installation; this leaf owns what that entry point proves. There
is no second example, compatibility entry point, or source-tree-only import
path.

## Authority

- [v1 design](../../V1_DESIGN.md)
- [v1 executable specification](../../V1_EXECUTABLE_SPEC.md)
- [ADR 0016](../../decisions/0016-uniform-workspace-and-consumer-actions.md)
- [release design](../release/design.md)

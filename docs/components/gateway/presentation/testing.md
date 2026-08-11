# Gateway per-destination presentation testing

Focused tests mirror the owner at `tests/gateway/test_presentation.py` and
prove per-destination transformation/suppression, immutable identity and
attachment authority, bounded content/metadata, finite capacity/lifetime and
cancellation, fixed redacted diagnostics, and unchanged absent behavior.

They also prove that projection checkpointability is mapped once to the fixed
O1 origin by the presentation owner, and that its post-claim result is a typed
presented/suppressed/failed decision with no repository, owner token, or
idempotency transition callback. Gateway/idempotency code consumes that result
to complete suppression or release a pre-Channel failure; checkpoint code
remains the authority for the subsequent CAS.

Crash/recovery integration proves suppression completes idempotency before the
checkpoint owner receives its CAS decision, completed recovery converges
without reinvocation, a
pre-completion failure releases only a pre-side-effect claim, and live-only
output never checkpoints. Non-projection origins do not invoke O1.

Ownership tests prove the focused `imagent.gateway.presentation` owner exposes
the exact contract objects, the finite Gateway package root does not expose
them, and the historical `imagent.outbound_presentation` module is not
importable.

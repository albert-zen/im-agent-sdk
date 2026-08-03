# Gateway per-destination presentation testing

Focused tests mirror the owner at `tests/gateway/test_presentation.py` and
prove per-destination transformation/suppression, immutable identity and
attachment authority, bounded content/metadata, finite capacity/lifetime and
cancellation, fixed redacted diagnostics, and unchanged absent behavior.

Crash/recovery integration proves suppression completes idempotency before
checkpoint CAS, completed recovery converges without reinvocation, a
pre-completion failure releases only a pre-side-effect claim, and live-only
output never checkpoints. Non-projection origins do not invoke O1.

Ownership tests prove `imagent.gateway` exposes the exact owner contract
objects and that the historical `imagent.outbound_presentation` module is not
importable.

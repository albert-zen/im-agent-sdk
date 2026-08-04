# Gateway projection checkpoints testing

Checkpoint conformance must prove:

- each Thread/Conversation route advances independently, with stable delivery
  identity derived from authoritative identity rather than text or time;
- ordinary route writes preserve a checkpoint and cannot silently clear,
  replace, or advance it;
- a stale expected checkpoint fails without changing any other route;
- an accepted delivery or durable O1 suppression completes idempotency before
  checkpoint CAS;
- authoritative recovery converges a completed stable delivery whose crash
  left the CAS behind, without resending or reinvoking O1;
- `in_flight`, unknown, failed, and live-only decisions never advance it;
- missing or expired checkpoints produce explicit bounded recovery degradation
  instead of an unbounded archive scan; and
- opaque item IDs are never ordered to infer advancement from duplicate live
  observations.

Focused derivation/ownership evidence is
`tests/gateway/projection/test_checkpoints.py`; it proves stable output for
the same stable identities, exact Gateway projection facade identity, and the
absence of the historical `imagent.projections` symbol. Cross-leaf checkpoint
evidence remains in `tests/test_projection_hardening.py`,
`tests/test_projection_routing.py`, and `tests/test_storage.py` while their
focused mechanical moves are pending.

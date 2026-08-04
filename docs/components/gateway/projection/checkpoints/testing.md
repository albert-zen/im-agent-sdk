# Gateway projection checkpoints testing

Checkpoint conformance must prove:

- each Thread/Conversation route advances independently, with stable delivery
  identity derived from authoritative identity rather than text or time;
- ordinary route writes preserve a checkpoint and cannot silently clear,
  replace, or advance it;
- a stale expected checkpoint fails without changing any other route;
- an accepted delivery or durable O1 suppression completed by the idempotency
  owner precedes checkpoint CAS;
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
absence of the historical `imagent.projections` module. It constructs the
narrow checkpoint authority with an in-memory route repository and proves
fresh completion CAS, authoritative convergence from `already_completed`,
live duplicate non-convergence, in-flight/live-only non-advancement,
same-item idempotence, competing expected-current CAS, and independent
per-destination progress without ordering opaque item IDs. Cross-leaf
checkpoint evidence remains in `tests/gateway/projection/test_hardening.py`,
`tests/gateway/routing/test_projection_integration.py`, and
`tests/gateway/persistence/test_sqlite.py`.

# Gateway projection recovery testing

Recovery conformance must prove:

- replay-capable and no-replay Applications take their distinct documented
  ordering paths, with live observation installed before history when replay is
  absent;
- cursor expiry, sequence gaps, missing checkpoints, and bounded page/item
  exhaustion remain explicit degraded facts rather than synthetic success or an
  unbounded archive read;
- a new route gets only a bounded baseline, while an existing route reconciles
  toward its own checkpoint without weakening other routes;
- baseline/recovery completes before that route drains live events, and a
  failure keeps its bootstrap fence closed;
- completed idempotency can converge a lagging checkpoint during authoritative
  recovery, while `in_flight` or live-only output cannot;
- subscription/recovery failure uses bounded retry and is isolated per Thread;
  one Channel destination failure does not restart observation;
- native request snapshots are reconciled only for the affected Thread and
  only when the Application advertises authoritative support; and
- no-snapshot reconnect/restart leaves request recovery explicitly degraded
  and never manufactures pending requests from correlations.

Current evidence: `tests/test_recovery.py`, `tests/test_projection_hardening.py`,
and `tests/test_projection_routing.py`. The target mirrored suite is
`tests/gateway/projection/test_recovery.py`.

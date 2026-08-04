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
- completed idempotency can give the checkpoint owner bounded authoritative
  evidence to converge a lagging checkpoint, while `in_flight` or live-only
  output cannot;
- subscription/recovery failure uses bounded retry and is isolated per Thread;
  one typed permanent or retryable Channel destination failure does not
  restart observation and does not block a healthy destination, while an
  injected checkpoint/repository failure restarts only the affected Thread's
  existing worker and converges through authoritative recovery;
- the focused supervisor returns capped retry and typed gap/degradation facts
  while observation remains the sole owner of subscription open, consumption,
  close, and resubscription;
- count limits reject zero, negative, boolean, and non-integer values, while
  retry bounds reject boolean, non-numeric, non-finite, negative, and reversed
  ranges without leaking a generic type error;
- native request snapshots are reconciled only for the affected Thread and
  only when the Application advertises authoritative support; and
- no-snapshot reconnect/restart leaves request recovery explicitly degraded
  and never manufactures pending requests from correlations.

Focused recovery evidence is `tests/gateway/projection/test_recovery.py`,
including bounded route reads, supervisor classification/backoff, and typed
request-snapshot coordination. Genuine worker, route-delivery, acceptance,
and checkpoint integration evidence remains in
`tests/test_projection_hardening.py` and `tests/test_projection_routing.py`.
The focused suite also proves that the Gateway projection facade re-exports
the exact owner objects and that the historical `imagent.recovery` module is
unavailable in a clean process.

# Gateway projection observation testing

Observation conformance must prove:

- concurrent first routes for one Application/Thread create and register one
  worker, while different Threads remain isolated;
- a positive active-Thread capacity admits the final distinct `ThreadRef`,
  lets same-Thread starters/waiters join that worker, and rejects only another
  Thread before subscription, authoritative reconciliation/checkpoint,
  presentation, or Channel delivery; task termination between same-Thread
  admission and ensure must retain that identity until the caller completes;
- cancellation of a same-Thread waiter leaves the shared worker and its slot
  intact; terminal, cancelled, and start-failed workers release worker-owned
  capacity and health entries; a worker terminal during pending acceptance must
  retain the fence, lock, and buffer until the final input owner persists the
  correlation and drains it; the established restore boundary clears all
  process-local acceptance tracking before route/checkpoint recovery;
- every live subscriber has an independent finite queue, and a slow or
  overflowed subscriber neither steals from nor blocks another subscriber or
  an Application socket/read callback;
- a subscriber overflow creates a typed recovery gap instead of an SDK event
  log, unbounded buffer, or silent sequence claim;
- `message.completed` does not end a multi-message Turn, and only explicit
  terminal Turn events do;
- baseline delivery precedes live draining for each route without creating a
  second content queue;
- one destination delivery failure does not restart the Thread worker or block
  other routes, whereas subscription/recovery failures use bounded backoff;
- live-only presentation shares route ordering but has a distinct stable event
  identity, never enters authoritative recovery, and never advances a
  checkpoint; and
- a buffered event accepted before Turn-correlation persistence stays bounded;
  overflow preserves terminal inbound state and enters recovery.

Current evidence: `tests/gateway/projection/test_observation.py`,
`tests/test_projection_hardening.py`, and `tests/test_projection_routing.py`.
The target mirrored suite is `tests/gateway/projection/test_observation.py`.

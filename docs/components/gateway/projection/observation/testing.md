# Gateway projection observation testing

Observation conformance must prove:

- concurrent first routes for one Application/Thread create and register one
  worker, while different Threads remain isolated;
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

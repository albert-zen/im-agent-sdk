# Projections and recovery testing

Current tests prove independent fan-out, explicit Turn terminal events, honest
ordering/replay declarations, foreground/remembered routing, and restart
reconciliation from authoritative history.

Protect these historical and known failure modes:

- one shared queue causing subscribers to steal events;
- a slow subscriber blocking a socket/read callback;
- `message.completed` terminating a multi-message Turn;
- cross-Thread counters producing false gaps;
- accepting an expired cursor without authoritative recovery;
- manufacturing replay/sequence support;
- input switching accidentally removing a remembered projection route;
- restart losing output because route state was not rebuilt.
- concurrent first observers regressing the current one-worker/no-orphan
  behavior;
- first observation or restart scanning/delivering an unbounded archive;
- a live completion during baseline history reading reversing baseline/live
  order or delivering twice;
- route refresh clearing a projection checkpoint, and missing/expired
  checkpoints silently causing an unbounded scan;
- concurrent Turns inheriting the latest inbound message's reply correlation;
- an external Turn without IM origin inheriting any previous reply target;
- a worker exiting on an exception with no health signal or automatic
  resubscription;
- one route's Channel delivery failure killing or restarting the Application
  subscription;
- worker health conflating infrastructure state with Agent Turn/request truth;
- an unbounded subscriber queue hiding slow-delivery memory pressure.

The explicit worker-concurrency, bounded-scan, bootstrap ordering, checkpoint,
per-Turn reply, failure-isolation, and worker-supervision cases are required
but not yet present at the 9fca3dd baseline. They are acceptance criteria for
[Issue #14](https://github.com/albert-zen/im-agent-sdk/issues/14). Bounded
delivery/backpressure tests belong to
[Issue #12](https://github.com/albert-zen/im-agent-sdk/issues/12).

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_event_fanout \
  tests.test_recovery \
  tests.test_projection_routing -v
```

When a native adapter changes event mapping, run its adapter contract and
vertical-slice tests as well.

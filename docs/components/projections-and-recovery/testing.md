# Projections and recovery testing

Current tests prove independent fan-out, explicit Turn terminal events, honest
ordering/replay declarations, foreground/remembered routing, bounded
authoritative reconciliation, checkpoint persistence, per-Turn reply
correlation, and failure isolation.

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
- two Conversations steering the same native Turn replacing its original
  reply destination, including after SQLite restart;
- a steer dispatching before its expected correlation exists, or a repository
  upsert silently replacing immutable correlation state;
- post-acceptance correlation or buffered-event failure releasing an inbound
  claim and creating a duplicate native Turn on Channel redelivery;
- native dispatch cancellation/response loss being mistaken for pre-dispatch
  failure, and secondary drain failure hiding the primary accepted-input
  error;
- an external Turn without IM origin inheriting any previous reply target;
- a worker exiting on an exception with no health signal or automatic
  resubscription;
- one route's Channel delivery failure killing or restarting the Application
  subscription;
- worker health conflating infrastructure state with Agent Turn/request truth;
- an unbounded subscriber queue hiding slow-delivery memory pressure.
- a request response being accepted from a destination where request delivery
  failed or never occurred;
- one failed request destination blocking another destination or creating an
  authorization correlation;
- a slow destination reopening correlation state after another destination
  already won the native response;
- a no-snapshot Application emitting an unobserved request during Gateway
  startup;
- `request.resolved` incorrectly terminating its Turn;
- restart/reconnect manufacturing pending requests without a native snapshot.

These cases are covered by `test_projection_hardening.py`,
`test_projection_routing.py`, `test_event_fanout.py`, `test_recovery.py`, and
`test_storage.py`. Gateway vertical slices additionally prove that Zen App
Server and T3 `AcceptedTurn` identities reach a reply-capable QQ projection;
the flat/no-reply fake Channel profile proves that reply context remains
optional and destination-safe. Native rendering details stay adapter tests.
Bounded delivery/backpressure and receipt-aware retry are tested in the
delivery planning and coordination component; projection tests prove this
component enters that same injected Coordinator path.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_projection*.py" -v
uv run python -m unittest discover -s tests -p "test_event_fanout.py" -v
uv run python -m unittest discover -s tests -p "test_recovery.py" -v
```

When a native adapter changes event mapping, run its adapter contract and
vertical-slice tests as well.

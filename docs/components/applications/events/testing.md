# Application events testing

Focused ownership coverage is `tests/applications/test_events.py`.
`tests/test_event_fanout.py` retains adapter and Gateway integration evidence.

Verify required stable event identity, optional ordering fields only when their
native scope is truthful, canonical event discriminants, and schema parity.
Fan-out tests must prove independent subscriptions receive the same live event,
publication does not await a consumer, and one finite queue overflow removes
only that subscription and raises a typed gap. They also prove no synthetic
cursor, transcript, or retained event log is introduced.

Event tests must prove that canonical event payloads can consume the exact
Applications-owned `AgentMessage` while keeping event envelope/order/fan-out
ownership in `applications.events`; the event module must not define or export
an Application model duplicate.

Recovery/adapter scenarios must prove that gaps trigger native-authoritative
replay/history reconciliation and that `message.completed` does not replace an
explicit terminal Turn event. The tests also prove the owner import remains
Gateway-independent and the legacy facades preserve exact nominal identities.

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.test_events -v
PYTHONPATH=src uv run python -m unittest discover -s tests -p 'test_event_fanout.py' -v
```

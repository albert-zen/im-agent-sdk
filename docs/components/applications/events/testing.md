# Application events testing

Focused ownership and pure fan-out coverage is
`tests/applications/test_events.py`. Native App Server fan-out/reset evidence
is mirrored in `tests/applications/adapters/test_codex.py`; Gateway
observation, startup admission, and request-gap evidence is kept in the
Gateway projection/lifecycle owners. The former root fan-out file is
intentionally absent and is not an internal compatibility path.

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
Gateway-independent, the explicit facades preserve exact nominal identities,
and the facade modules contain no duplicate implementation or lazy lookup.

```sh
PYTHONPATH=src uv run python -m unittest tests.applications.test_events -v
PYTHONPATH=src uv run python -m unittest tests.applications.adapters.test_codex -v
PYTHONPATH=src uv run python -m unittest tests.gateway.projection.test_observation -v
PYTHONPATH=src uv run python -m unittest tests.gateway.projection.test_recovery -v
PYTHONPATH=src uv run python -m unittest tests.gateway.test_lifecycle -v
```

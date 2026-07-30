# Agent Application adapters testing

Every adapter should prove:

- instance-scoped Project/Thread references;
- managed/flat/fixed project shape;
- create/read/list and side-effect-free pagination;
- Thread lookup independent from native activation;
- actual archive/permanent deletion semantics;
- stable client-message ID round-trip;
- concurrent input returns the distinct native `AcceptedTurn` identity for
  each call;
- canonical user and Agent message events;
- multiple completed messages before an explicit terminal Turn event;
- fan-out-safe subscriptions;
- authoritative snapshot/history plus live reconciliation;
- honest replay, cursor, sequence, attachment, request, and unsupported
  capabilities.

Run:

```sh
uv run python -m unittest discover -s tests -p "test_adapter_contracts.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_client.py" -v
uv run python -m unittest discover -s tests -p "test_appserver_transport.py" -v
uv run python -m unittest discover -s tests -p "test_gateway_vertical_slice.py" -v
uv run python -m unittest discover -s tests -p "test_recovery.py" -v
```

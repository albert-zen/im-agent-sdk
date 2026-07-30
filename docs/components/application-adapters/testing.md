# Agent Application adapters testing

Every adapter should prove:

- instance-scoped Project/Thread references;
- managed/flat/fixed project shape;
- create/read/list and side-effect-free pagination;
- Thread lookup independent from native activation;
- actual archive/permanent deletion semantics;
- stable client-message ID round-trip;
- canonical user and Agent message events;
- multiple completed messages before an explicit terminal Turn event;
- fan-out-safe subscriptions;
- authoritative snapshot/history plus live reconciliation;
- honest replay, cursor, sequence, attachment, request, and unsupported
  capabilities.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_adapter_contracts \
  tests.test_imcodex_appserver \
  tests.test_t3_client \
  tests.test_attachments \
  tests.test_recovery \
  tests.test_event_fanout \
  tests.test_gateway_vertical_slice -v
```

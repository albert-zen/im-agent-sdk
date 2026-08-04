# Gateway in-memory persistence testing

Focused delivery-submission tests prove:

- the first reservation acquires and an identical repeat returns the same
  record without a second winner;
- concurrent reservation has one winner;
- stable-ID reuse with changed root immutable identity fails explicitly;
- a repeated reservation with the same destination snapshot set is
  order-independent, while a changed destination ID or snapshot fails;
- destination updates require an existing submission/destination and the
  expected prior state;
- successful updates preserve every other immutable field and validate the
  complete record;
- no content, artifact bytes/path, credential, callback, or retry work item is
  introduced;
- imports use the persistence owner and proactive delivery behavior remains
  unchanged.

Finite record capacity remains a separate explicit follow-up gap. Its behavior
slice must cover default Gateway composition without weakening the completed
memory/SQLite reservation identity parity.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_memory \
  tests.gateway.delivery.test_proactive_delivery \
  tests.gateway.delivery.test_proactive_ingress -v
```

The existing binding, projection, and request-correlation suites continue to
own their process-local repository tests until those implementations move in
their own focused slices. Every slice also runs the full repository gates.

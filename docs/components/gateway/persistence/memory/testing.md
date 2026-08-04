# Gateway in-memory persistence testing

Focused binding tests prove:

- the first put assigns revision one and later puts increment monotonically;
- validation happens before mutation;
- stale expected revisions reject put and delete through the exact shared
  `BindingConflict` type without changing the stored record;
- delete with the current revision removes only that Conversation;
- concurrent process-local operations are serialized by the repository lock;
- restart constructs an empty repository and claims no durable recovery;
- no route, Application worker, transcript, or product JSON state is created.

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
- `max_records` is positive and finite; at the boundary, identical replay and
  destination CAS still work while the next distinct identity fails before
  mutation;
- concurrent distinct reservations at the final slot have exactly one winner,
  with no eviction of terminal, retryable, in-flight, or unknown evidence;
- no content, artifact bytes/path, credential, callback, or retry work item is
  introduced;
- imports use the persistence owner and proactive delivery behavior remains
  unchanged.

Gateway composition tests additionally prove the stable default, invalid-limit
construction rejection, explicit limit wiring, and that an injected repository
is not wrapped or reconfigured by the default-only limit. A fresh process-local
repository starts empty; SQLite behavior and schema remain unchanged.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_memory \
  tests.gateway.delivery.test_proactive_delivery \
  tests.gateway.delivery.test_proactive_ingress -v
```

The projection and request-correlation suites continue to own their
process-local repository tests until those implementations move in their own
focused slices. Every slice also runs the full repository gates.

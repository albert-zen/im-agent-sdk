# Gateway idempotency persistence testing

Idempotency tests must prove:

- concurrent claims for one stable scope/key have exactly one acquired owner;
- completed and active/protected claims return the distinct typed outcomes;
- only the current owner may refresh, protect, complete, or release a claim;
- stale owners cannot mutate a replacement owner after durable reclaim;
- refresh accepts only `in_flight` and cannot revive
  `side_effect_started` or `completed`;
- the process-local implementation performs no time-based reclaim and starts
  empty after restart;
- SQLite may reclaim only stale `in_flight`, while elapsed time never turns
  `side_effect_started` into repeat permission;
- Gateway releases only a failure proven before side effects and preserves the
  protected record for cancellation/unknown outcomes after the fence; and
- no claim row or process-local record contains content, media, exception
  text, rendered presentation, callback, or retry work.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_storage \
  tests.gateway.test_admission -v
```

The later mechanical move adds
`tests/gateway/persistence/test_idempotency.py` without changing the common
repository Port or SQLite behavior.

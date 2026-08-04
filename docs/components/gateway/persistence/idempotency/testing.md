# Gateway idempotency persistence testing

Idempotency tests must prove:

- concurrent claims for one stable scope/key have exactly one acquired owner;
- completed and active/protected claims return the distinct typed outcomes;
- only the current owner may refresh, protect, complete, or release a claim;
- stale owners cannot mutate a replacement owner after durable reclaim;
- refresh accepts only `in_flight` and cannot revive
  `side_effect_started` or `completed`;
- `max_records` accepts only a positive non-boolean integer and rejects an
  invalid bound before a claim can mutate state;
- concurrent distinct identities competing for the final slot have one
  acquired owner and one `IdempotencyCapacityError`, while a new identity at
  capacity changes no existing record;
- at capacity, an existing completed record still replays, an active or
  protected record still joins, and the current owner can refresh, protect,
  complete, or release it under the existing fence;
- completed and ambiguous evidence is never evicted to make space; a release
  can free only the caller's nonterminal claim, and a fresh process still
  starts empty;
- the process-local implementation performs no time-based reclaim and starts
  empty after restart;
- SQLite may reclaim only stale `in_flight`, while elapsed time never turns
  `side_effect_started` into repeat permission;
- Gateway releases only a failure proven before side effects and preserves the
  protected record for cancellation/unknown outcomes after the fence; and
- the composed default receives the positive
  `GatewayLimits.idempotency_max_records` bound, so inbound capacity failure
  precedes media preparation and outbound capacity failure precedes planning or
  Channel work; and
- no claim row or process-local record contains content, media, exception
  text, rendered presentation, callback, or retry work.

Run:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_idempotency \
  tests.gateway.test_admission -v
```

Focused process-local claim evidence lives in
`tests/gateway/persistence/test_idempotency.py`. SQLite restart/reclaim
 evidence remains with its transaction owner in `tests/gateway/persistence/test_sqlite.py`; the
move does not change the common repository Port or SQLite behavior.

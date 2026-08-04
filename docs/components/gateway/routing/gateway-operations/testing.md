# Gateway operations testing

Gateway operation conformance must prove:

- every accepted operation and result validates as its exact typed variant;
- unknown, malformed, and unsupported operations produce stable explicit
  failures rather than a generic handler fallback;
- one Conversation's mutations serialize and unrelated Conversations can run
  independently;
- replay tests distinguish operations with a proved state postcondition from
  revisionless operations that may write a new revision again, and never infer
  generic deduplication from `operation_id`;
- read-only Application/resource listing has no binding, route, observation,
  activation, or Application mutation side effect;
- binding and observation remain independent operations except for the narrow
  documented `foreground_only` route preparation;
- native Application operations are passed through the Application contract
  and are not interpreted as Gateway business logic;
- ControllerActions can invoke the public typed operations without receiving a
  repository or mutable Gateway context; and
- the per-Conversation lock registry has a finite concurrency-safe lifetime,
  never evicts a lock while it is owned or awaited, and fails explicitly if
  safe capacity cannot be acquired; and
- an existing key can join at capacity, a new key receives retryable
  `capacity_exhausted` before any side effect, and cancellation removes usage
  exactly once;
- claimed inbound capacity rejection preserves the I2/no-I2 pre-acceptance
  claim transition and can never authorize native input; and
- product-only commands and generic extension hooks are absent.

Current evidence is in `tests/test_gateway_operations.py` and
`tests/test_gateway_vertical_slice.py`. The target mirrored suite is
`tests/gateway/routing/test_operations.py`; moving it belongs to a later
behavior-preserving slice.

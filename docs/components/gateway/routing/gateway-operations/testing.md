# Gateway operations testing

Gateway operation conformance must prove:

- the focused owner and every finite public facade expose the same exact
  operation/result objects, signatures, and `typing.get_type_hints` results in
  clean processes regardless of import order;
- the Gateway-root finite resolver accepts only its nine aggregate
  operation/validator names, caches the exact focused-owner object after first
  access, and rejects unknown names; root observation helper annotations
  resolve to the exact projection-route owner values and request helper
  annotations resolve to the exact request-correlation owner values in a clean
  process, while request convergence constructs `RequestResponseRouted` and
  the locked route delegate returns that exact result;
- the aggregate delegates binding mutations, projection observation, and
  request-response routing through explicit typed owner methods or ports, with
  no generic repository/context parameter and no duplicated owner validator;
- every accepted operation and result validates as its exact typed variant;
- unknown, malformed, and unsupported operations produce stable explicit
  failures rather than a generic handler fallback;
- one Conversation's mutations serialize and unrelated Conversations can run
  independently;
- replay tests distinguish operations with a proved state postcondition from
  generationless operations that may write a new generation again, and never infer
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

The mechanics-only bound, same-key join, independent-key progress, and exact
cancellation cleanup are focused in `tests/gateway/test_concurrency.py`.
Operation tests retain the behavior-specific evidence for `ConversationRef`
key selection, the configured Gateway limit, failure mapping, and the exact
pre-side-effect boundary; they do not duplicate the primitive implementation.

Focused owner evidence is in `tests/gateway/routing/test_operations.py`.
Integration evidence remains in `tests/gateway/test_operations_integration.py` and
`tests/gateway/test_vertical_slice.py`; the focused extraction must preserve
all of those branches rather than replacing or deleting them.

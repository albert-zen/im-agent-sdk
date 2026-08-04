# Gateway persistence state contracts testing

State-contract tests must prove:

- bindings preserve one stable Conversation identity, validate consistent
  Application/Project/Thread references, and reject invalid revisions;
- route identity binds one stable route ID to one exact Thread/Conversation
  endpoint pair;
- checkpoint identity and time are present or absent together and are never
  interpreted as an orderable bridge cursor;
- Turn reply correlations preserve immutable stable identity, and request
  correlations reject invalid response shapes while accepting one declared
  bridge state;
- delivery reservation identity includes every destination delivery ID and
  its complete route snapshot independent of tuple order;
- mutable outcome/receipt fields do not alter reservation identity, while any
  changed root fingerprint or snapshot does;
- values contain no transcript item, prompt/response body, message/artifact
  content, credential, callback, or retry job; and
- JSON schemas and Python values remain compatible at their public boundary.

The missing finite cardinality limits for delivery destinations and request
questions/choice IDs remain an explicit capacity gap. A later behavior slice
must add focused boundary and over-limit tests before claiming that every
persisted collection is bounded.

Repository conformance tests cover the complete monotonic request-state graph;
the passive value tests continue to validate one declared state without
claiming native pending-request authority.

Current focused coverage is:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_contracts \
  tests.test_projection_routing \
  tests.gateway.persistence.test_submission_identity -v
```

When the mechanical extraction lands, the same cases move to
`tests/gateway/persistence/test_state_contracts.py`; the move must preserve the
stable public facade and remove the old internal owner.

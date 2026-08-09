# Gateway persistence state contracts testing

State-contract tests must prove:

- bindings preserve one stable Conversation identity, validate consistent
  Application/Project/Thread references, and reject invalid generations,
  including Boolean values in bindings and repository preconditions;
- every Thread-bearing state value has mandatory Project ancestry, including
  fixed/flat workspaces, and rejects a partial or mismatched hierarchy;
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
  content, credential, callback, retry job, free-form delivery error, or
  receipt/item/segment detail text;
- durable delivery classifications stay closed and stable receipt identities
  reject values beyond the identifier bound; and
- JSON schemas and Python values remain compatible at their public boundary.

The proactive target/intent/result vocabulary and validator are tested through
their `gateway.delivery.proactive` owner; passive submission state is tested
through `gateway.persistence.state_contracts` and its persistence facade.

Focused boundary tests accept exactly 64 delivery destinations, 32 request
questions, and 64 approval or per-question choice IDs. They reject one more
member before fingerprinting/reservation/SQL or Channel/Application work, and
prove that rejection preserves the immutable delivery snapshot and existing
repository state.

Repository conformance tests cover the complete monotonic request-state graph;
the passive value tests continue to validate one declared state without
claiming native pending-request authority.

Focused coverage is:

```sh
PYTHONPATH=src python -m unittest \
  tests.gateway.persistence.test_state_contracts -v
```

The mirrored cases preserve the stable persistence facade and verify that the
old internal owners are absent.

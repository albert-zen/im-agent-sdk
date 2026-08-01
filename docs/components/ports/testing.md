# Python ports testing

Ports are verified through structural typing, fake implementations, and common
adapter conformance suites.

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_adapter_contracts -v
pyright src tests
```

When a repository port changes, also run binding/storage tests. When an
Application or Channel port changes, run every concrete adapter's focused and
vertical-slice tests.

The reusable contract suite proves that a modern Channel accepts the admission
callback through its lifecycle. Focused native/Gateway tests must additionally
prove that durable admission happens before media preparation, a missing lease
stops work without preventing a later reclaim attempt, preparation failure
releases the lease, handoff transfers terminal ownership to Gateway, and the
legacy two-callback startup shape remains usable during migration.

Application input coverage must distinguish safe pre-dispatch failure from a
sent request with an unknown native outcome. The latter remains sticky across
redelivery and restart. Every implementation must accept the default
continuation preference, invoke the hook once immediately before native
mutation, and return a result matching the authorized disposition/policy.

Delivery Port coverage must include atomic concurrent reservation, identity
conflict, immutable snapshots, per-destination compare-and-set updates, and
restart persistence. Authorization fakes must not infer scope from caller
Metadata.

Reject changes that:

- add a method implemented by only one product without reuse evidence;
- make a lower Port import Gateway or a concrete integration;
- hide unsupported behavior behind optional duck typing without a capability
  or explicit error.

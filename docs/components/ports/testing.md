# Python ports testing

Ports are verified through structural typing, fake implementations, and common
adapter conformance suites.

Run:

```sh
PYTHONPATH=src:tests python -m unittest tests.test_adapter_contracts -v
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
legacy message-only startup shape remains usable during migration. The modern
shape accepts only message and admission callbacks; no Channel contract, fake,
or native wrapper imports or stores `GatewayOperation`.
The Interaction owner and `imagent.adapters` compatibility export must be the
same `ChannelAdapter` Protocol object; the compatibility module must not retain
a second class definition.

Startup-validation coverage checks structural capability detection, all four
SDK native implementations, bounded explicit failures, configuration parity
with `start()`, and repeatability around a completed lifecycle. A fake Channel
without the optional protocol remains a valid `ChannelAdapter`; callers must
not treat capability absence as successful validation.

Channel diagnostics coverage likewise keeps `diagnostic_facts()` outside the
required lifecycle Port and proves missing, invalid, raising, and mismatched
providers cannot fail or inject identity into a snapshot.

Application input coverage must distinguish safe pre-dispatch failure from a
sent request with an unknown native outcome. The latter remains sticky across
redelivery and restart. Every implementation must accept the default
continuation preference, invoke the hook once immediately before native
mutation, and return a result matching the authorized disposition/policy.

Delivery Port coverage must include atomic concurrent reservation, identity
conflict, immutable snapshots, per-destination compare-and-set updates, and
restart persistence. A bounded process-local implementation must distinguish
typed capacity failure from conflict, reject before mutation, and retain
existing replay/state updates at the boundary. Authorization fakes must not
infer scope from caller Metadata.

An ADR 0015 Port must additionally prove its exact stage position, immutable
identity fields, bounded input/output, absent-provider compatibility, and
stage-specific replay/failure rule. Contract suites must reject a generic
pipeline callback or an extension receiving Gateway/repository authority.

Reject changes that:

- add a method implemented by only one product without reuse evidence;
- make a lower Port import Gateway or a concrete integration;
- hide unsupported behavior behind optional duck typing without a capability
  or explicit error.

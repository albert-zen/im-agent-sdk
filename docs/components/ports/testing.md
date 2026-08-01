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

Application input coverage must distinguish safe pre-dispatch failure from a
sent request with an unknown native outcome. The latter remains sticky across
redelivery and restart.

Delivery Port coverage must include atomic concurrent reservation, identity
conflict, immutable snapshots, per-destination compare-and-set updates, and
restart persistence. Authorization fakes must not infer scope from caller
Metadata.

Reject changes that:

- add a method implemented by only one product without reuse evidence;
- make a lower Port import Gateway or a concrete integration;
- hide unsupported behavior behind optional duck typing without a capability
  or explicit error.

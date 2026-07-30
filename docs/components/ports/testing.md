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

Reject changes that:

- add a method implemented by only one product without reuse evidence;
- make a lower Port import Gateway or a concrete integration;
- hide unsupported behavior behind optional duck typing without a capability
  or explicit error.

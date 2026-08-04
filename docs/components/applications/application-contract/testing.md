# Application contract testing

The current test owner is `tests/test_adapter_contracts.py`; the target focused
owner is `tests/applications/test_contract.py`. Mechanical migration must keep
the language-neutral schemas and exact public facade identity aligned.

Required scenarios prove managed/flat/fixed Project shape, instance-scoped
references, Thread lookup independent of native activation, typed operation
result discrimination, and a stable client-message ID. Every concrete adapter
must accept the default continuation preference, call the pre-dispatch callback
once immediately before mutation, and return only the disposition/correlation
policy it actually performed.

The suite distinguishes a known pre-dispatch rejection from a dispatched
unknown outcome. It covers Codex native steer where supported and truthful
`started/create_new` behavior for Zen/T3, including correlation authorization
and a post-acceptance mismatch that cannot retarget or retry native input.
It also checks that subscriptions are Application-owned and that no contract
adds Gateway/product-command authority.

Run the focused suite with:

```sh
PYTHONPATH=src uv run python -m unittest tests.test_adapter_contracts -v
```

Changes to this public Port also require every affected native adapter,
contract/schema validation, static typing, component-map checks, and the full
repository verification required by `AGENTS.md`.

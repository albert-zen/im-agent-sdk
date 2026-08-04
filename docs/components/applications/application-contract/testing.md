# Application contract testing

The focused owner is `tests/applications/test_contract.py`; the concrete-adapter
conformance suite remains affected evidence in
`tests/conformance/test_adapter_contracts.py`. The tests must keep the
language-neutral schemas and exact public facade identity aligned.

The focused ownership tests assert that every family member is one exact object
across `imagent.applications`, `imagent.applications.contract`, and the
temporary `imagent.contracts` facade. They also assert that
`AgentApplicationAdapter` and `ApplicationInputDispatchHandler` are exact
objects across the Applications and `imagent.adapters` facades; the old module
has no Protocol or callback implementation. Contract imports do not initialize
a concrete adapter, add a Gateway dependency, or use a lazy facade workaround.
Runtime `typing.get_type_hints` preserves the model fields and the native
continuation, pre-dispatch fence, pending-request, and single
Thread-subscription signatures.

Required scenarios prove managed/flat/fixed Project shape, instance-scoped
references, strong `ThreadRef` validation, frozen/slot model behavior, history
and live `AgentMessage` parity, Thread lookup independent of native activation,
typed operation result discrimination, and a stable client-message ID. Every concrete adapter
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
PYTHONPATH=src uv run python -m unittest tests.applications.test_contract tests.conformance.test_adapter_contracts -v
```

Changes to this public Port also require every affected native adapter,
contract/schema validation, static typing, component-map checks, and the full
repository verification required by `AGENTS.md`.

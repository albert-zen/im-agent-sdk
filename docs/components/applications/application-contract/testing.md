# Application contract testing

The focused owner is `tests/applications/test_contract.py`; the concrete-adapter
conformance suite remains affected evidence in
`tests/conformance/test_adapter_contracts.py`. The tests must keep the
language-neutral schemas and exact public facade identity aligned.

The focused contract suite also proves that `ApplicationInputOutcomeUnknown`
has one exact class owner in `imagent.applications.contract`, is exposed by
the finite `imagent.applications` facade and the explicit `imagent.contracts`
facade as the same object, inherits directly from `RuntimeError`, preserves
the `(message, cause)` constructor and `.cause`, and leaves
`imagent.contracts.errors` absent after the historical implementation is
retired.

The focused ownership tests assert that every contract-family member is one
exact object across `imagent.applications` and
`imagent.applications.contract`. They assert that only
`ApplicationInputOutcomeUnknown` is present in `imagent.contracts`, and that
the retired Application names fail in clean subprocesses after each relevant
import order. `AgentApplicationAdapter` and
`ApplicationInputDispatchHandler` remain exact between the owner and the root
Applications facade, but are absent from `imagent.adapters`. Contract imports
do not initialize a concrete adapter or Gateway implementation, and the
finite root facade does not eagerly load concrete adapters.
Runtime `typing.get_type_hints` preserves the model fields and the native
continuation, pre-dispatch fence, pending-request, and single
Thread-subscription signatures.

Required scenarios prove managed/flat/fixed Project-management shape, the one
stable fixed/flat workspace Project and canonical-root fingerprint, instance-
scoped references, mandatory strong `ThreadRef` Project ancestry, frozen/slot
model behavior, history
and live `AgentMessage` parity, Thread lookup independent of native activation,
typed operation result discrimination, and a stable client-message ID. No
Project-less construction, event, history, request, route, or persisted row is
accepted. Negative ancestry cases also place foreign Turn and `AgentMessage`
values inside otherwise valid catch-up/history/event envelopes and require the
semantic validators to fail closed. Every concrete adapter
must accept the default continuation preference, call the pre-dispatch callback
once immediately before mutation, and return only the disposition/correlation
policy it actually performed.

The suite distinguishes a known pre-dispatch rejection from a dispatched
unknown outcome using the canonical Applications exception. It covers Codex
native steer where supported and truthful
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

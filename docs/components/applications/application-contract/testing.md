# Application contract testing

The focused owner is `tests/applications/test_contract.py`; the concrete-adapter
conformance suite remains affected evidence in
`tests/conformance/test_adapter_contracts.py`. The tests must keep the
language-neutral schemas and exact public facade identity aligned.

The focused contract suite also proves that `ApplicationInputOutcomeUnknown`
has one exact class owner in `imagent.applications.contract`, is exposed by
the finite `imagent.applications` facade as the same object, inherits directly
from `RuntimeError`, and preserves the `(message, cause)` constructor and
`.cause`. The historical `imagent.contracts` package is absent.

The focused ownership tests assert that every contract-family member is one
exact object across `imagent.applications` and
`imagent.applications.contract`. They assert that the retired cross-layer
modules fail in clean subprocesses after each relevant import order.
`AgentApplicationAdapter` and
`ApplicationInputDispatchHandler` remain exact between the owner and the root
Applications facade. Contract imports
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
and Zen native steer where supported and truthful
`started/create_new` behavior for T3, including correlation authorization
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

`ApplicationInputRejected` has one canonical owner/facade identity and accepts
only `turn_ended` or `thread_busy`; client and I2 suites verify authoritative
negative acceptance versus ambiguous outcomes and terminal claim behavior.

Queued model/union evidence lives in `tests/applications/test_queued_input.py`: real
queued item identity, no placeholder Turn, admitted same-Thread Turn constraints,
closed states, SHA-256 dispatch fingerprints and exact facade identity. Existing
protocol reflection tests preserve the default while intentionally widening the
acceptance family. The queued implementation slice is not full Gateway support.

Type-check protocol-typed unchanged consumers, not only concrete adapters:
omitted continuation, both explicit ordinary enum literals, the historical
narrow callback alias, and the distinct dynamic-plan callback must retain their
intended result types. `_check_public_protocol_input_typing` in the queued model
tests is an executable pyright assertion set.


## Queued lifecycle

`tests/applications/adapters/test_zen_queue_bulk.py` proves mixed per-ID canonical
states, independent absence, duplicate positions, global scope/identity checks,
and original fingerprint retention after edits. `test_zen_queue_lifecycle.py`
proves one read per cohort, bounded inputs, no cache/implicit resume, exact
control identity, known cancellation/admission and reserved-input uncertainty.
It also exercises event-before-response capture, concurrent same-Thread resume,
caller cancellation, overflow, reset, snapshot tail/watermark ordering, and
close joining owned work. Production client tests prove one-shot cancellation
for malformed success, errors, timeout, cancellation and close.

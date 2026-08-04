# Documentation map

Start with:

1. [Vision](VISION.md) for product purpose, non-goals, and the threshold for
   common abstractions.
2. [Architecture](ARCHITECTURE.md) for components, ownership, dependency
   direction, and system flows.
3. [Accepted decisions](decisions/README.md) for cross-component choices that have
   completed design review.
4. The approved [three-layer component tree](components/README.md) and its
   machine-readable [component map](components/component-map.yml).
5. The affected component's design and testing documents below.
6. [Engineering support](engineering/README.md) for conformance, schema,
   maintainability, AgentKit, and release mechanics.
7. The neutral [reference consumer onboarding](onboarding/README.md) for a
   runnable composition of the public contracts.

`REUSE.md` owns source provenance and extraction rules. `ROADMAP.md` contains
future or unresolved work and is not authority for current runtime behavior.
JSON Schema under `schemas/v1/` is the language-neutral contract surface.
Active ownership migrations use explicit maps under `docs/migrations/`; the
current [Issue #9 SDK-side transfer map](migrations/issue-9-imcodex-owner-transfer.md)
separates SDK ownership work from the later IMCodex consumer migration.

## Change navigation

| Change area | Read first | Validate first |
|---|---|---|
| Interaction message values in `contracts/**`, `schemas/v1/messages.schema.json` | [messages design](components/interaction/messages/design.md), [testing](components/interaction/messages/testing.md), [protocol](components/contracts/protocol.md#message-envelopes) | `tests/interaction/test_messages.py`, schema validator |
| Common operation values in `contracts/**`, `schemas/v1/operations.schema.json` | [operations design](components/interaction/operations/design.md), [testing](components/interaction/operations/testing.md), [protocol](components/contracts/protocol.md#typed-operations) | `test_contracts.py`, schema validator |
| Remaining unsplit `contracts/**` and contract schemas | [contracts design](components/contracts/design.md), [protocol](components/contracts/protocol.md), [testing](components/contracts/testing.md) | `test_contracts.py`, schema validator |
| `adapters.py` | [ports design](components/ports/design.md), [testing](components/ports/testing.md) | adapter contract kit and static typing |
| `interaction/media.py` and media schemas | [media design](components/interaction/media/design.md), [testing](components/interaction/media/testing.md) | Interaction media, contract, and vertical-slice tests |
| `gateway/delivery/planning.py` | [planning design](components/gateway/delivery/planning/design.md), [testing](components/gateway/delivery/planning/testing.md) | deterministic planning golden tests |
| `gateway/delivery/coordination.py`, shared `keyed_locks.py` | [coordination design](components/gateway/delivery/coordination/design.md), [testing](components/gateway/delivery/coordination/testing.md) | ordering, backpressure, retry, cancellation, and receipt aggregation tests |
| `gateway/delivery/outcome_observation.py` | [outcome observation design](components/gateway/delivery/outcome-observation/design.md), [testing](components/gateway/delivery/outcome-observation/testing.md) | O2 attempt identity, bounded facts, detached failure, and cleanup-order tests |
| `gateway/delivery/submissions.py` | [submissions design](components/gateway/delivery/submissions/design.md), [testing](components/gateway/delivery/submissions/testing.md) | immutable reservation, destination CAS, sticky unknown, and restart receipt tests |
| `gateway/delivery/proactive_authorization.py` | [proactive authorization design](components/gateway/delivery/proactive-authorization/design.md), [testing](components/gateway/delivery/proactive-authorization/testing.md) | credential bounds, typed scope, revoke, denial, and exact facade tests |
| `gateway/delivery/proactive.py` | [proactive delivery design](components/gateway/delivery/proactive-delivery/design.md), [testing](components/gateway/delivery/proactive-delivery/testing.md) | route pinning, concurrency, retry safety, restart, redaction, and O2 tests |
| `gateway/presentation.py` | [Gateway presentation design](components/gateway/presentation/design.md), [testing](components/gateway/presentation/testing.md) | O1 destination identity, suppression crash convergence, and bounded runtime tests |
| `gateway/composition.py` and Gateway construction | [Gateway composition design](components/gateway/composition/design.md), [testing](components/gateway/composition/testing.md) | frozen groups, defaults, bounds, exact exports, removed historical import, and no-extension parity |
| `gateway/lifecycle.py` startup helpers and package-root Gateway start/stop | [Gateway lifecycle design](components/gateway/lifecycle/design.md), [testing](components/gateway/lifecycle/testing.md) | startup FIFO, overflow, rollback races, shutdown, cleanup, and the remaining package-root orchestration gap |
| `gateway/admission.py` | [Gateway admission design](components/gateway/admission/design.md), [testing](components/gateway/admission/testing.md) | stable identity, pre-media claim, owner fencing, duplicate, and handoff tests |
| `gateway/input/content_transformation.py`, `gateway/input/failure_presentation.py`, and native input dispatch | [Gateway input subtree](components/gateway/input/README.md) and the affected leaf design/testing docs | I1 replay/identity, dispatch fence/correlation, or I2 phase/claim tests |
| Gateway package root (`gateway/__init__.py`) | [gateway design](components/gateway/design.md), [testing](components/gateway/testing.md) | gateway operation and vertical-slice tests |
| `events.py`, `projection_routes.py`, `projection_runtime.py`, `request_projection_runtime.py`, `projections.py`, `gateway/projection/checkpoints.py`, `gateway/projection/recovery.py` | [Gateway projection](components/gateway/projection/README.md): [observation](components/gateway/projection/observation/design.md), [checkpoints](components/gateway/projection/checkpoints/design.md), [request correlation](components/gateway/projection/request-correlation/design.md), and [recovery](components/gateway/projection/recovery/design.md) | fan-out, projection routing, request correlation, recovery tests |
| `gateway/persistence/repository_contracts.py`, `gateway/persistence/memory.py`, `gateway/persistence/row_mapping.py`, `gateway/persistence/sqlite.py` | [Gateway persistence](components/gateway/persistence/README.md), [persistence design](components/persistence/design.md), [testing](components/persistence/testing.md), [row-mapping design](components/gateway/persistence/row-mapping/design.md) | Gateway persistence memory, row-mapping, SQLite-owner, and submission tests |
| `controllers/**` | [Controller subtree](components/interaction/controllers/README.md) and the affected leaf design/testing docs | Controller contract, registry, common-command, or request-presentation focused tests, including formal-facade identity and absent historical-path import-order evidence |
| `channels/**` | [Interaction Channel subtree](components/interaction/channels/README.md), affected leaf design/testing docs, and applicable native adapter page | Channel contract, ingress, outbound-delivery, diagnostics, and native seam tests |
| `applications/**` | [Applications navigation](components/applications/README.md), the affected common leaf, then the precise [adapter subtree](components/applications/adapters/README.md) leaf | adapter contract, native client, event, recovery tests |
| `applications/diagnostics.py` and adapter/presentation diagnostic contracts | [Applications diagnostics](components/applications/diagnostics/design.md), [testing](components/applications/diagnostics/testing.md), then the affected App Server/presentation leaf | exact owner identity/order, no-Gateway imports, redaction/bounds, adapter and wheel smoke |
| `examples/reference_consumer/**` or `docs/onboarding/**` | [Reference consumer onboarding](onboarding/README.md), then the affected Interaction, Gateway, and Applications leaf | `tests/gateway/test_reference_consumer.py`, module smoke, and the full repository gates |
| `interaction/diagnostics.py`, `applications/diagnostics.py`, Channel diagnostic providers, and the transition facade | [Interaction diagnostics design](components/interaction/diagnostics/design.md), [Applications diagnostics](components/applications/diagnostics/design.md), [Channel diagnostics design](components/interaction/channels/diagnostics/design.md), and [transition diagnostics](components/diagnostics/design.md) | redaction, bounded-cardinality, reconnect/overflow, exact identity/import-order, and aggregate health tests |
| `interaction/testing/**` and the `imagent.testing` facade | [conformance design](engineering/testing-and-conformance/design.md), [testing](engineering/testing-and-conformance/testing.md) | `tests/conformance/test_adapter_contracts.py`, package/import-order checks |
| `schemas/v1/**`, `scripts/validate_schemas.py` | [schema-conformance design](engineering/schema-conformance/design.md), [testing](engineering/schema-conformance/testing.md), and [contracts protocol](components/contracts/protocol.md) | schema validator, focused schema-conformance tests, and runtime contract tests |
| AgentKit lifecycle and routing | [AgentKit design](engineering/agentkit/design.md), [testing](engineering/agentkit/testing.md) | `tests/engineering/test_agentkit.py`, AgentKit doctor/check, and review guidance |
| CI, root guidance, docs navigation, and maintainability budgets | [repository maintainability design](engineering/repository-maintainability/design.md), [testing](engineering/repository-maintainability/testing.md) | `tests/engineering/test_repository_maintainability.py`, mapping, link, inventory, and maintainability checks |
| `pyproject.toml`, package facades, `py.typed`, wheels, clean install | [release design](engineering/release/design.md), [testing](engineering/release/testing.md) | `tests/engineering/test_release.py`, build, and isolated wheel smoke |

Use `python scripts/agentkit.py orient --path <path>` for the executable
version of this routing. `agentkit.yml` intentionally maps global Vision,
Architecture, and Decisions changes across product components.

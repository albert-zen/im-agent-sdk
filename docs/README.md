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

`REUSE.md` owns source provenance and extraction rules. `ROADMAP.md` contains
future or unresolved work and is not authority for current runtime behavior.
JSON Schema under `schemas/v1/` is the language-neutral contract surface.
Active ownership migrations use explicit maps under `docs/migrations/`; the
current [Issue #9 SDK-side transfer map](migrations/issue-9-imcodex-owner-transfer.md)
separates SDK ownership work from the later IMCodex consumer migration.

## Change navigation

| Change area | Read first | Validate first |
|---|---|---|
| Interaction message values in `contracts/**`, `schemas/v1/messages.schema.json` | [messages design](components/interaction/messages/design.md), [testing](components/interaction/messages/testing.md), [protocol](components/contracts/protocol.md#message-envelopes) | `test_contracts.py`, schema validator |
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
| Gateway package root (`gateway/__init__.py`) | [gateway design](components/gateway/design.md), [testing](components/gateway/testing.md) | gateway operation and vertical-slice tests |
| `events.py`, `projection_routes.py`, `projection_runtime.py`, `projections.py`, `recovery.py` | [projection/recovery design](components/projections-and-recovery/design.md), [testing](components/projections-and-recovery/testing.md) | fan-out, projection routing, recovery tests |
| `bindings.py`, `sqlite_rows.py`, `storage.py` | [persistence design](components/persistence/design.md), [testing](components/persistence/testing.md) | binding and storage tests |
| `controllers/**` | [Controller subtree](components/interaction/controllers/README.md) and the affected leaf design/testing docs | Controller contract, registry, common-command, or request-presentation focused tests |
| `channels/**` | [Interaction Channel subtree](components/interaction/channels/README.md), affected leaf design/testing docs, and applicable native adapter page | Channel contract, ingress, outbound-delivery, and native seam tests |
| `applications/**` | [Application adapter design](components/application-adapters/design.md), [testing](components/application-adapters/testing.md), applicable native page | adapter contract, native client, event, recovery tests |
| `diagnostics.py` and diagnostic providers | [diagnostics design](components/diagnostics/design.md), [testing](components/diagnostics/testing.md) | redaction, bounded-cardinality, reconnect/overflow, and aggregate health tests |
| `testing/**` | [conformance design](components/testing-and-conformance/design.md), [testing](components/testing-and-conformance/testing.md) | adapter contract kit tests |
| `schemas/v1/**`, `scripts/validate_schemas.py` | [contracts protocol](components/contracts/protocol.md), [repository testing](components/repository-maintainability/testing.md) | schema validator and contract tests |
| AgentKit, CI, root guidance, docs navigation | [repository maintainability design](components/repository-maintainability/design.md), [testing](components/repository-maintainability/testing.md) | AgentKit doctor/check and mapping tests |

Use `python scripts/agentkit.py orient --path <path>` for the executable
version of this routing. `agentkit.yml` intentionally maps global Vision,
Architecture, and Decisions changes across product components.

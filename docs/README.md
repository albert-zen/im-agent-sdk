# Documentation map

Start with:

1. [Vision](VISION.md) for product purpose, non-goals, and the threshold for
   common abstractions.
2. [Architecture](ARCHITECTURE.md) for components, ownership, dependency
   direction, and system flows.
3. [Accepted decisions](decisions/README.md) for cross-component choices that have
   completed design review.
4. The affected component's design and testing documents below.

`REUSE.md` owns source provenance and extraction rules. `ROADMAP.md` contains
future or unresolved work and is not authority for current runtime behavior.
JSON Schema under `schemas/v1/` is the language-neutral contract surface.

## Change navigation

| Change area | Read first | Validate first |
|---|---|---|
| `contracts/**`, `schemas/v1/**` | [contracts design](components/contracts/design.md), [protocol](components/contracts/protocol.md), [testing](components/contracts/testing.md) | `test_contracts.py`, schema validator |
| `adapters.py` | [ports design](components/ports/design.md), [testing](components/ports/testing.md) | adapter contract kit and static typing |
| `attachments.py` | [attachments/media design](components/attachments-and-media/design.md), [testing](components/attachments-and-media/testing.md) | attachment trust and vertical-slice tests |
| `gateway.py` | [gateway design](components/gateway/design.md), [testing](components/gateway/testing.md) | gateway operation and vertical-slice tests |
| `events.py`, `projections.py`, `recovery.py` | [projection/recovery design](components/projections-and-recovery/design.md), [testing](components/projections-and-recovery/testing.md) | fan-out, projection routing, recovery tests |
| `bindings.py`, `storage.py` | [persistence design](components/persistence/design.md), [testing](components/persistence/testing.md) | binding and storage tests |
| `controllers/**` | [controllers design](components/controllers/design.md), [testing](components/controllers/testing.md) | Slash Controller tests |
| `channels/**` | [Channel adapter design](components/channel-adapters/design.md), [testing](components/channel-adapters/testing.md), applicable native page | Channel contract and native seam tests |
| `applications/**` | [Application adapter design](components/application-adapters/design.md), [testing](components/application-adapters/testing.md), applicable native page | adapter contract, native client, event, recovery tests |
| `testing/**` | [conformance design](components/testing-and-conformance/design.md), [testing](components/testing-and-conformance/testing.md) | adapter contract kit tests |
| AgentKit, CI, root guidance, docs navigation | [repository maintainability design](components/repository-maintainability/design.md), [testing](components/repository-maintainability/testing.md) | AgentKit doctor/check and mapping tests |

Use `python scripts/agentkit.py orient --path <path>` for the executable
version of this routing. `agentkit.yml` intentionally maps global Vision,
Architecture, and Decisions changes across product components.

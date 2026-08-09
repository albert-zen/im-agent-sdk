# V1 repository audit and transformation DAG

Status: design-baseline audit at `8558068093e917c6b19cdc71049989cf78723dc4`

This audit compares the repository with `V1_DESIGN.md`. Existing behavior is
not retained merely because it has tests. Safety evidence is reused; conflicting
semantics and public shapes are replaced without compatibility layers.

## Structural divergences

### P0: two resource trees instead of one

Current `ThreadRef.project_ref` is optional. Binding validation rejects a
Project for flat/fixed modes, and many tests construct Project-less Threads.
This conflicts with the uniform Application → Project/Workspace → Thread →
Turn model.

Required transformation:

- make `ThreadRef` ancestry unambiguous and always Project-scoped;
- expose one stable workspace Project from fixed/flat adapters;
- make list/get truthful for that scope while creation/native switching remain
  unsupported;
- migrate schemas, events, history, requests, bindings, routes, correlations,
  adapters, persistence rows, and tests together.

### P0: product onboarding policy inside Gateway input

Current ordinary input selects the only Application when none is bound and
creates/binds a Thread when none is selected. That policy belongs to the
consumer and creates native resources as a hidden side effect of message
dispatch.

Required transformation:

- unbound input returns an explicit missing-binding outcome;
- remove implicit sole-Application selection and implicit Thread creation;
- expose the same explicit actions to commands, native UI interactions, and
  consumer onboarding policy;
- preserve durable admission and unknown-outcome protection while removing the
  hidden workflow.

### P0: consumers manually compose cross-authority workflows

Current `CommandHandlerActions` exposes only low-level
`execute_application`, `execute_gateway`, and `get_binding`. Common `/new`
creates a Thread, binds it, and observes it manually. There is no workflow
receipt, phase fingerprint, partial-success result, or safe resume boundary.

Required transformation:

- introduce `ApplicationActions` and Conversation-scoped
  `ConversationActions`;
- implement `create_and_select_project` and `create_and_bind_thread` once in
  Gateway;
- add minimal memory/SQLite effect receipts for store-only Gateway mutations,
  primitive native mutations, request response, and workflows, with atomic
  Gateway commits or a durable `native_side_effect_started` fence, stable phase
  IDs, monotonic binding-generation CAS, and conflict detection;
- represent success, failure, partial, and unknown explicitly;
- use finite non-evicting receipt capacity, failing before effects when full;
- remove hand-coded workflow sequences from common/product handlers.

### P0: current Project-creation worker preserves the old resource model

Commit `bef9cec5a6fea93e0c8c96f3ab8682074db83213` provides useful evidence:
bounded `CreateProject(cwd)`, `ProjectCreated`, stable operation identity,
managed fake idempotency, honest unsupported native adapters, schema coverage,
and no guessed endpoint.

It is not mergeable as the v1 contract because it retains optional
`ProjectRef`, Project-less fixed/flat semantics, and primitive-only results.
Its reusable implementation and tests should be transplanted or revised after
the uniform resource contract lands.

### P1: persistence is exposed as repository wiring

Current consumers construct `GatewayRepositories` with several optional
repositories. Defaults can silently mix durable and process-local state. There
is no transaction boundary for hierarchical binding plus foreground route
preparation or for workflow phase receipts.

Required transformation:

- expose one `MemoryGatewayStore` and one `SQLiteGatewayStore` public choice;
- keep focused internal repository protocols for ownership and tests;
- require one coherent durability domain per Gateway;
- atomically commit binding, foreground route, successor generation, and the
  workflow receipt's terminal binding phase;
- enforce one active runtime per store namespace with a renewable lease and
  owner token plus monotonic fencing epoch and freshness checked by every
  mutation using store-authoritative time;
- keep takeover conservative after a fenced native call: negative lookup is
  sticky unknown unless native idempotency or terminal operation status makes
  reconciliation safe;
- prohibit configurations that appear durable while critical bridge state is
  process-local.

### P1: lifecycle and public facade are implementation-shaped

Current public runtime is `ImAgentGateway` with explicit `start()`/`stop()` but
no preferred async context manager or single `run()` convenience. The package
facades mirror incremental component moves rather than the minimal consumer
surface.

Required transformation:

- expose one finite `Gateway` public runtime;
- add async-context lifecycle and one convenience runner over the same object;
- retain explicit start/stop for embedding;
- remove old facade/internal aliases instead of maintaining dual APIs;
- ensure clean-wheel examples import only the final public surface.

### P1: the reference consumer bypasses its intended contract

The current example is useful routing evidence but directly creates Threads on
the fake Application and directly emits native Turns. It uses a flat
Project-less Application, switches only away, and demonstrates restart by
reusing the same in-memory objects.

Required transformation is defined by `V1_EXECUTABLE_SPEC.md`: public Project
and Thread workflows, ordinary Channel input, switch-back, SQLite reconstruction,
capability honesty, typed failures, and clean-wheel execution.

### P1: authority documents conflict

Pre-v1 Vision, Architecture, ADR 0001, protocol, component docs, schemas, and
onboarding encode optional Project ancestry, low-level Controller executors,
and the old reference path. Passing link/schema tests currently proves internal
consistency with the wrong target.

Required transformation:

- make `V1_DESIGN.md`, ADR 0016, and the executable specification the leading
  authority;
- update global and component authorities before each behavior block;
- delete obsolete statements rather than explaining two versions;
- update AgentKit mapping so the new authorities route every affected owner.

## Transformation DAG

### A. Authority and uniform resource contract

1. Reconcile Vision, Architecture, protocol, ADRs, component docs, schemas,
   and AgentKit mapping with ADR 0016.
2. Make Project/Workspace ancestry mandatory in resources, Application
   operations/events/history, and Gateway bridge state.
3. Update deterministic fakes and adapter conformance for managed/fixed/flat
   honest scopes.
4. Revise the Project-creation evidence on top of this contract.

This block is serial and uses a strong model because it changes the public
resource model and nearly every stable identity path.

### B. Outcome algebra, store, and effect receipts

1. Define language-neutral success/failure/partial/unknown results and stable
   action fingerprints.
2. Define focused internal store protocols plus public memory/SQLite stores.
3. Add monotonic Conversation binding generations and atomic store-only
   Gateway mutation plus terminal-receipt transaction semantics.
4. Add primitive native-action and request-response receipts plus create/select
   and create/bind workflow receipts, convergence, conflicts, cancellation
   fences, and shared non-evicting bounded capacity.
5. Add the exclusive store-namespace runtime lease and stale-owner mutation
   fencing, including paused-old-owner takeover tests, before exposing either
   store as production-ready.

This block starts only after A stabilizes. Store implementation and outcome
contract may be split only if their files and transaction contract are already
fixed.

### C. Scoped consumer actions and commands

1. Expose `ApplicationActions` and `ConversationActions` without concrete
   adapter/Gateway context.
2. Implement reads, primitive mutations, Gateway mutations, and the two safe
   workflows.
   Managed Project deletion is included when honestly advertised; it does not
   silently clear Gateway bindings.
3. Route common commands and neutral product handlers through the scoped
   surface.
4. Remove low-level manual workflow composition from handlers.
5. Gate principal identity, Conversation isolation, and absence of adapter,
   store, repository, and `Any` escape hatches at type and runtime surfaces.

### D. Policy-free ordinary input

1. Remove implicit Application selection and Thread creation.
2. Return explicit missing-binding through the classified failure path.
3. Preserve admission, I1/I2, prefer-active-Turn, correlation, and no-retry
   invariants.
4. Prove a consumer Controller can implement optional onboarding explicitly
   without creating a second dispatch path.

### E. Executable vertical consumer

Implement the minimal golden path in `V1_EXECUTABLE_SPEC.md`: managed CWD
Project, create/select, create/bind, ordinary text round-trip, two
Conversations, switch and switch-back, one worker, diagnostics, and shutdown.
From this block onward every capability PR updates the same executable.

### F. Projection and durable recovery

Move the existing stable-ID, fan-out, checkpoint, correlation, suppression,
gap, and recovery algorithms onto the uniform resource/store/action model.
Add fresh-object SQLite restart evidence and inspect persisted state for
authority leakage.

### G. Requests, media, artifacts, and proactive delivery

Reconnect existing safety implementations through the one public runtime and
store. Preserve delivered-destination request authorization, typed trust,
consumer-owned artifact bytes/cleanup, pinned proactive routes, and sticky
unknown outcomes.

### H. Lifecycle, diagnostics, adapters, and release surface

Complete async context/run convenience, adapter conformance, bounded shutdown,
redacted diagnostics, wheel API, `py.typed`, production checklist, and
troubleshooting around the one final facade.

### I. Delete pre-v1 architecture and final review

Delete Project-less branches, implicit onboarding, old facades, duplicate
docs/examples, compatibility shims, orphan tests, and multi-owner code. Run
full gates, clean-wheel executable specification, strong clean-context review,
fix findings, and review the fixes again.

## Reuse rule

Existing tests are classified by semantic evidence, not by pass count:

- retain and move tests for stable identity, claim fencing, bounded capacity,
  one-worker fan-out, checkpoint convergence, correlation immutability,
  unknown outcomes, delivery order, and shutdown;
- rewrite tests whose fixture assumes Project-less Threads, implicit resource
  creation, manual Controller workflows, mixed durability, or internal API
  access;
- remove import/facade tests for surfaces that do not belong to the final SDK.

# V1 repository audit and transformation DAG

Status: DAG blocks A–D implemented; blocks E–I remain transformation targets

This audit compares the repository with `V1_DESIGN.md`. Existing behavior is
not retained merely because it has tests. Safety evidence is reused; conflicting
semantics and public shapes are replaced without compatibility layers.

## Structural divergences

### Resolved in A: two resource trees instead of one

The old optional-Project tree has been deleted. `ProjectRef`, `ThreadRef`, and
`TurnRef` now use the exact nested identities from `V1_DESIGN.md`. Events,
history, requests, operations, bindings, routes, correlations, delivery
targets, row mapping, schemas, fakes, adapters, and tests carry the same stable
Project ancestry.

Landed evidence:

- managed Applications preserve native Project identity and reject native
  Threads without it;
- fixed/flat Applications expose exactly one immutable workspace Project,
  advertise list/get as `fallback`, and return typed unsupported management;
- workspace identity uses configured stable IDs plus a typed SHA-256
  fingerprint over one adapter-canonicalized execution root;
- legacy Project-less persistence rows fail closed instead of being inferred
  from path, display text, timestamps, or empty sentinels.

### Resolved in D: policy-free ordinary input

Ordinary input now requires one explicit complete Application/Project/Thread
binding and invokes one canonical dispatch runtime. `MissingBindingError`
carries the stable `missing_binding` classification for absent/incomplete
hierarchy; `StaleBindingError` carries `stale_binding` when the bound
Application is unregistered or authoritative Project/Thread reads return
`not_found`. Both use the existing pre-acceptance/I2 path without binding,
effect-receipt, route, resource, native-input, or fallback-delivery mutation.
Required admission claim/release or completion bookkeeping remains intact.

Landed evidence:

- sole-Application discovery, implicit Thread creation, default CWD, and
  hand-rendered Gateway onboarding errors are absent from ordinary input;
- binding shape and authoritative Project/Thread existence resolution precede
  I1, route preparation, worker start, and native dispatch;
- a Controller backed by one coherent B store session receives only C's scoped
  `ConversationActions`, can explicitly create/select and create/bind, and can
  return `None` to pass the same original Message through the same dispatch
  path;
- two Conversations retain independent binding/route/input ancestry, while C,
  the coherent store, and the ordinary-input resolver reject foreign
  Conversation/resource hierarchy;
- successful and terminally replayed scoped route mutations reconcile the
  projection-owned runtime against current store authority; common `/new`
  starts observation before the consumed command returns, and activation
  failure becomes a typed partial outcome instead of false success; opaque
  generation leases serialize same-route action lifecycles, prevent stale
  completion from opening a newer fence, and retire blocked delivery when the
  route is removed; lifecycle generation and worker-liveness checks reject
  activation racing/following shutdown and allow restart replay to converge;
  and
- durable admission and unknown-outcome protection remain intact, including
  released pre-acceptance replay, terminal accepted replay, cancellation
  fences, prefer-active-Turn behavior, and Turn/reply correlation.

### Resolved in B/C: scoped actions, workflows, and durable execution seam

The public low-level Controller action protocols and default Slash wrapper are
removed. Commands and native UI integrations use the same immutable
`ConversationActions`; trusted native administration uses
`ApplicationActions`. Common `/new` submits one
`CreateBindingWorkflowRequest`, and primitive delete never clears a binding.

Landed C boundary:

- introduce `ApplicationActions` and Conversation-scoped
  `ConversationActions`;
- implement `create_and_select_project` and `create_and_bind_thread` once in
  Gateway;
- map every mutation to the exact B-owned `GatewayEffectExecutor` request seam,
  with no C store/session/repository access;
- represent success, failure, partial, and unknown explicitly;
- use finite non-evicting receipt capacity, failing before effects when full;
- remove hand-coded workflow sequences from common/product handlers; and
- record concrete memory/SQLite replay, lease, unknown-outcome, and workflow
  CAS as the required B integration acceptance rather than implementing a
  second transaction path in C.

Block B now supplies the closed outcomes, fingerprints, durable receipts, and
typed store/native/workflow executor seam, with memory/SQLite parity coverage.
Block C consumes that exact seam and owns the scoped public actions and removal
of manual consumer composition. For foreground-safe clear-observation, C sets
`StoreMutationPlan.route_delete_condition` to
`RouteDeleteCondition.UNLESS_BOUND_TO_ROUTE_THREAD`; B evaluates it atomically
against resulting binding state and C performs no store/session pre-read.
Block D now composes those actions for ordinary input only when all Gateway
runtime owners share one already-leased coherent B store session. Loose or
mixed repositories still reject Controller configuration rather than
substituting the old generic wrapper. The executable reference Controller and
final public store-acquisition/lifecycle surface remain block E/H work; D adds
no second action or dispatch implementation.

### Resolved in A: Project-creation evidence revised onto the uniform model

Commit `bef9cec5a6fea93e0c8c96f3ab8682074db83213` provides useful evidence:
bounded `CreateProject(cwd)`, `ProjectCreated`, stable operation identity,
managed fake idempotency, honest unsupported native adapters, schema coverage,
and no guessed endpoint.

The bounded `CreateProject`/`ProjectCreated` variants and stable operation ID
seam were transplanted without the old optional Project shapes. The managed
fake replays one deterministic result for the same operation ID. Fixed/flat
and currently unsupported managed adapters return a typed `unsupported`
failure. Durable effect receipts, crash fencing, and workflow outcomes remain
block B work.

### P1: persistence is exposed as repository wiring

The retiring runtime still constructs `GatewayRepositories` with several optional
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

Block B now exposes the coherent `GatewayStore`, `MemoryGatewayStore`, and
`SQLiteGatewayStore` choices with one lease-fenced session and transaction
domain. Later composition blocks must consume that port and delete the old
repository-wiring surface; they must not add a bridge between the two APIs.

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

The current example is useful routing evidence but still directly creates
Threads on the fake Application and directly emits native Turns. Its flat
Application now exposes one stable workspace Project and Project-scoped
Thread/Turn/event identities, but it still switches only away and demonstrates
restart by reusing the same in-memory objects.

Required transformation is defined by `V1_EXECUTABLE_SPEC.md`: public Project
and Thread workflows, ordinary Channel input, switch-back, SQLite reconstruction,
capability honesty, typed failures, and clean-wheel execution.

### P1: authority documents conflict

The authority/resource-contract part of Vision, Architecture, protocol,
component docs, schemas, onboarding, and AgentKit mapping is reconciled with
ADR 0016. Low-level Controller executors and the old reference workflow remain
later-block evidence.

Required transformation:

- make `V1_DESIGN.md`, ADR 0016, and the executable specification the leading
  authority;
- update global and component authorities before each behavior block;
- delete obsolete statements rather than explaining two versions;
- update AgentKit mapping so the new authorities route every affected owner.

## Transformation DAG

### A. Authority and uniform resource contract

Status: complete.

1. Reconcile Vision, Architecture, protocol, ADRs, component docs, schemas,
   and AgentKit mapping with ADR 0016.
2. Make Project/Workspace ancestry mandatory in resources, Application
   operations/events/history, and Gateway bridge state.
3. Update deterministic fakes and adapter conformance for managed/fixed/flat
   honest scopes.
4. Revise the Project-creation evidence on top of this contract.

The clean boundary consumed by B was the immutable
`WorkspaceIdentity(project_ref, root_fingerprint)` exposed by fixed/flat
Application summaries. B persists and compares that value, rejects the same
workspace ID with a changed fingerprint, and supplies coherent stores/effect
receipts. None of those semantics were added to the old repository bundle.

### B. Outcome algebra, store, and effect receipts

Status: complete.

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

Landed evidence includes the four-variant outcome schema, namespaced bounded
fingerprints, the exact `GatewayEffectExecutor` seam for C, one public
`GatewayStore` port with memory/SQLite parity, monotonic generation tombstones,
shared non-evicting receipt capacity, atomic Gateway/workflow commits, durable
native fences, conservative reconciliation, workspace fingerprint checks, and
exclusive store-time leases with monotonic epochs. SQLite maintenance is
default-deny outside schema initialization, and every focused mutation has an
in-transaction database-time guard even when it converges or affects zero
rows. Focused tests cover restart, commit-ack replay, durable-phase
cancellation, concurrent same-action fencing, unknown native effects, ABA,
capacity, route-only observation/clear, route-conflict rollback, crash-expiry
takeover, negative reconciliation after takeover, and stale owner rejection in
a fresh execution context. Hierarchical clears now carry closed transaction-
internal clear scope rather than caller-pre-read ancestors, and native action
authorization/capability checks use the executor's reserved-phase preflight so
terminal replay always precedes reauthorization. Later composition blocks
must acquire `GatewayStore`, construct `StoreBackedGatewayEffectExecutor` from
its leased session, and inject only `GatewayEffectExecutor` into C; neither B
nor C bridges the retiring repository bundle.

### C. Scoped consumer actions and commands

Status: implemented over the reviewed B executor/store seam; final public
Gateway factory and lifecycle wiring remain later DAG work.

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

Status: complete.

1. Remove implicit Application selection and Thread creation.
2. Return explicit missing- or stale-binding through the classified failure
   path.
3. Preserve admission, I1/I2, prefer-active-Turn, correlation, and no-retry
   invariants.
4. Prove a consumer Controller can implement optional onboarding explicitly
   without creating a second dispatch path.

Landed evidence includes the exact public `MissingBindingError` and
`StaleBindingError` identities and language-neutral `missing_binding`/
`stale_binding` codes, pre-acceptance release/I2 classification, zero-effect
incomplete-binding tests at every hierarchy depth, Memory/SQLite deleted
Project/deleted Thread/unregistered Application counterexamples, explicit C
workflow onboarding followed by the unchanged Message, stable accepted-message
replay, hostile binding-repository Conversation/resource ancestry rejection,
two-Conversation isolation, real registry common-`/new` observation and
terminal route-action replay/activation-failure evidence, post-receipt
cancellation join and failed-baseline fencing until replay, and retained
dispatch/correlation/cancellation/unknown-outcome suites. Obsolete fixtures now
create and bind their Threads explicitly; existing-Thread vertical evidence
waits for the correlated reply after any authoritative baseline recovery.

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

### J. Downstream experimental rewrites

Only after the SDK candidate passes I, create isolated experimental branches
and worktrees for IMCodex, IMT3, and IMZen. Pin each to the exact SDK candidate
commit/wheel and rewrite its bridge composition through the public v1 surface.
Keep product configuration, commands, permissions, credentials, and
presentation downstream; remove duplicated bridge authority from the
experimental path.

Run each consumer's native suite and a real public-path vertical scenario.
Generic defects return to the SDK and reopen the affected SDK gates/review;
consumer-specific policy does not migrate into Core. Do not merge these
experimental branches to downstream defaults without separate human approval.
Resolve repository provenance first when a checkout is not currently backed by
Git; do not create an ad-hoc repository merely to satisfy this DAG item.

## Reuse rule

Existing tests are classified by semantic evidence, not by pass count:

- retain and move tests for stable identity, claim fencing, bounded capacity,
  one-worker fan-out, checkpoint convergence, correlation immutability,
  unknown outcomes, delivery order, and shutdown;
- rewrite tests whose fixture assumes Project-less Threads, implicit resource
  creation, manual Controller workflows, mixed durability, or internal API
  access;
- remove import/facade tests for surfaces that do not belong to the final SDK.

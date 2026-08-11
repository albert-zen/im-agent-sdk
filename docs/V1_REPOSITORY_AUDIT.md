# V1 repository audit and transformation DAG

Status: DAG blocks A–H implemented; block I remains the SDK transformation target

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
  activation racing/following shutdown, while one post-preflight commit fence
  makes shutdown-before-write a no-mutation failure and lets an entered atomic
  commit finish before shutdown; the same fence covers a foreground workflow's
  terminal binding/route transaction after its native result is known, and
  public Memory/SQLite composition proves restart convergence without another
  native create;
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
public store-acquisition/lifecycle surface land in E; the broader lifecycle
failure matrix remains block H work. D and E add no second action or dispatch
implementation.

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

### Resolved in E: canonical lifecycle and executable public facade

The public `Gateway` now owns one coherent store lease, async-context and
explicit lifecycle, bounded diagnostics, scoped action factories, and
deterministic shutdown. Composition validates all limits, Application
capabilities, Channel/store/session structure, and stable identities before
external work; teardown continues through every owner exactly once while
preserving primary and cleanup failures. The installed reference consumer uses only that public
surface for explicit managed-CWD Project creation/selection, Thread
creation/binding, D's policy-free ordinary-input dispatch, two-Conversation
fan-out, switch and switch-back, and exact worker/delivery/shutdown evidence.
It does not directly create scenario resources on the Application or compose a
second runtime/store path. Block F extends that same consumer with a bounded
fresh-object SQLite reconstruction phase rather than a replacement runtime.

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

Status: complete.

Implement the minimal golden path in `V1_EXECUTABLE_SPEC.md`: managed CWD
Project, create/select, create/bind, ordinary text round-trip, two
Conversations, switch and switch-back, one worker, diagnostics, and shutdown.
From this block onward every capability PR updates the same executable.

The canonical consumer now runs that path through public `Gateway` composition,
one coherent store, scoped actions, native Channel ingress, the optional frozen
Controller, and authoritative Application observation. The same module runs
from the source tree and an isolated wheel. Focused evidence proves stable
resource replay, Conversation isolation in both switch directions, one
subscription per Thread, exact non-duplicated delivery cardinality, immediate
activation of action-created routes, bounded redacted diagnostics, lease-loss
revocation including blocked startup, terminal replay under later worker-capacity
drift, and zero owned work after shutdown. Block F adds fresh-object SQLite
reconstruction to this same executable, as recorded below.

### F. Projection and durable recovery

Status: complete.

The existing stable-ID fan-out, per-destination checkpoint/correlation/
suppression, typed-gap, bounded-retry, and authoritative-recovery algorithms
now run through the uniform Project-scoped resources, coherent leased store,
and canonical public Gateway lifecycle. One stable Thread still has one
Gateway-owned observation worker while independent destination routes retain
their own checkpoint and failure boundary.

Landed evidence includes replay-capable and live-first/no-replay ordering,
bounded recent baseline and existing-checkpoint scans, explicit missing/
expired/exhausted evidence, completed-idempotency checkpoint convergence,
live-only/in-flight non-convergence, per-Thread infrastructure recovery,
same-Thread acceptance-gap handling, pending-request snapshot honesty,
generation-fenced lifecycle cancellation, and Memory/SQLite repository parity.
The same installed public reference executable now closes and discards its
first Gateway, Channel, and SQLite store objects, creates authoritative output
while the Gateway is absent, constructs fresh objects over the same database,
and proves binding/route/checkpoint/receipt reconstruction without duplicate
Application input, delivery, or concurrent subscription. It inspects SQLite
through an exact `sqlite_schema` object/definition and column allowlist, a
consistent WAL-aware read snapshot, bounded type/cardinality/value-shape
validation, and stable bounded descriptor reads of database bytes and present
sidecars. Encoded, compressed, fragmented, BLOB, unexpected-object/row,
sidecar-appearance/disappearance, path-replacement, non-file-sidecar, and file-growth
counterexamples fail closed.
Public evidence additionally preserves binding generations, advances both
captured destination checkpoints to the missed stable item, retains completed
idempotency rows, and suppresses a duplicate prior input identity without an
Application call or delivery. Only the Application object whose bounded
authoritative history survives is reused.

### G. Requests, media, artifacts, and proactive delivery

Status: complete.

The existing request-correlation, media-planning/trust, artifact-outcome, and
proactive-delivery owners now run through the canonical public `Gateway`, its
single coherent `GatewayStore` session, and the same delivery Coordinator.
`ConversationActions.respond_request` authorizes against correlations created
only after accepted request delivery, then uses B's durable native-effect
fence; a non-recipient is rejected before Application work, native first-writer
truth wins, and terminal action replay performs no second native operation.
Startup reprojects only an Application's authoritative pending snapshot; when
that capability is absent, pre-existing open evidence becomes explicitly
stale.

The source and clean-wheel reference executable now covers a request delivered
to two Conversations, duplicate and restart behavior, exact proactive
principal scope, immutable two-destination route snapshots across a later
binding change, isolated accepted/unknown outcomes, sticky replay across fresh
SQLite objects, and unsupported/trust/digest/count/size/media/grouping failures
before the reference Channel's native-send boundary. Its injected,
consumer-owned bounded artifact ledger covers cancellation/failure release,
partial startup, restart sweep, per-destination outcome cleanup, and root
confinement. Exact schema/value and bounded WAL/sidecar byte inspection rejects
artifact bytes, paths, request/job content, credentials, and other
authority-owned data in SDK persistence. Focused Memory evidence and the same
SQLite executable retain one worker/subscription owner and D/F lifecycle,
store-fence, checkpoint, and recovery invariants. The executable also compares
the recoverable presentation signature on live and history-recovered output and
proves that a delivered live-only item leaves both destination completion
checkpoints unchanged.

Pass-1 counterexamples additionally prove that terminal/unknown submission
identity is origin-plus-caller-delivery scoped and wins before current
credential work: revoked credentials and same-token principal rotation replay
the authoritative Memory/SQLite result with zero resend. Public intents are
canonically snapshotted before their first await; bool/float declared sizes
fail before acquisition. The reference Channel opens rooted paths through a
no-follow descriptor chain and hashes the exact acquired bytes, closing the
resolve/read swap window. Retryable O2 outcomes retain consumer artifact
leases until an explicit retry reaches a terminal outcome. Ledger payload and
JSON replacement are fsync-backed, and startup enumeration fails closed at a
finite directory-entry bound before deleting any candidate.

Pass-2 counterexamples wire the same descriptor-bound acquisition through the
production Telegram, Feishu, Weixin, QQ, and T3 native submission paths. Each
submits the bytes from the opened descriptor after a deterministic pathname
swap; App Server advertises no attachment source and rejects its path-only
image protocol before native dispatch because it cannot preserve descriptor
identity. The reference consumer opens its ledger through one bounded,
no-follow descriptor beneath a pinned root and rejects ledger-file symlinks,
pathname replacement, and growth during startup.

### H. Lifecycle, diagnostics, adapters, and release surface

Status: complete.

The canonical `Gateway` now has one serialized terminal lifecycle for explicit
start/stop, async context, `wait_closed()`, and async `run()`. Caller/exit
cancellation joins the same close task; body failures remain primary; every
runtime owner uses one configured finite cleanup timeout and later owners still
run after failure, even when a hostile owner suppresses cancellation. Every
post-acquisition failure is projected without a raw exception graph, and all
partial owner starts are inside rollback. Fresh reconstruction, lease loss, startup overflow, late
callbacks, and concurrent transition races retain the D/F lifecycle and store
fences.

Diagnostics now bound direct projection aggregation to 4,096 records and
saturate counters at 1,000,000 while hostile records/iterables collapse to
fixed redacted facts. The reusable conformance ledger names all four shipped
Channels and all three concrete Application adapters; the Channel ledger also
executes the shared behavioral suite plus every native owner suite. The one finite lazy facade,
`__version__`, `py.typed`, wheel metadata/entry point, and exact public
identities are exercised in clean processes. All six isolated install profiles
run the same installed golden executable from a temporary CWD. Production
checklist and troubleshooting now cover limits, trust roots, store path,
workspace identity, lease/recovery, shutdown, diagnostics, and explicit
unsupported behavior without product policy.

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

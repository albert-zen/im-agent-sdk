# IM Agent SDK v1 architecture and consumer contract

Status: normative v1 target

This document defines the architecture and public consumer experience for the
first formal IM Agent SDK. It is derived from the confirmed product model and
system invariants, not from the current repository layout or existing Python
types. Pre-v1 code and documentation are implementation evidence to audit; when
they conflict with this design, they are changed or removed rather than
preserved through compatibility layers.

## Product promise

IM Agent SDK is a thin semantic bridge between IM Channels and Agent
Applications. It lets a user enter and continue one authoritative Agent Thread
from one or more IM Conversations without creating a second Agent runtime or a
second transcript.

The SDK standardizes resources, content, control intent, capabilities, bridge
state, observation, recovery, and delivery. A consumer supplies product policy:
which Applications and workspaces are available, which commands or native UI
actions exist, who may use them, what defaults are offered, and how failures
are presented.

The SDK is not an Agent runtime, transcript store, product command set,
permission engine, credential manager, artifact store, job scheduler, or
general orchestrator.

## One resource model

The v1 public resource hierarchy is unconditional:

```text
Application
  └─ Project / Workspace
       └─ Thread
            └─ Turn
```

Every Agent Application executes in a workspace/CWD context. Therefore every
public Thread has a stable `ProjectRef`; consumers never switch between a
Project-shaped tree and a Project-less tree.

Applications declare one Project mode:

- **managed** — the native Application owns discoverable Projects and may
  support evidenced creation or deletion;
- **fixed** — the adapter exposes exactly one stable configured workspace;
  discovery and reading are supported, creation and native switching are not;
- **flat** — the native protocol has no Project resource, but the adapter
  exposes exactly one stable workspace scope representing the Application's
  real execution context; it does not pretend that native project management
  exists.

The stable single scope in fixed/flat mode is an honest adapter projection of
real execution context, not a fabricated native capability. Its reference is
scoped by the configured Application instance, its mode remains visible, and
unsupported mutations fail explicitly.

Stable identity, never display text, path text, or timestamps, drives resource
equality and idempotency:

```text
ApplicationRef = application_instance_id
ProjectRef     = (application_instance_id, project_id)
ThreadRef      = (project_ref, thread_id)
TurnRef        = (thread_ref, turn_id)
ConversationRef = (channel_instance_id, conversation_id)
```

Native identifiers remain opaque. Adapters may derive the one fixed/flat
`project_id` from stable configured identity, but not from a mutable display
name or an uncanonicalized timestamp.

Fixed/flat configuration includes an explicit immutable `workspace_id` and a
fingerprint of its canonical execution root. Reusing the same workspace ID
with a changed fingerprint fails Gateway startup against persisted store
identity. An intentional workspace change uses a new workspace ID, making old
bindings explicitly stale instead of silently retargeting them. The store may
retain only the fingerprint; it need not retain the root path.

## Three authorities

### Interaction

Interaction owns typed inbound/outbound message envelopes, content, Channel
contracts, delivery receipts, Controller contracts, and local command
composition.

`Message` carries content. `Operation` or a typed action carries control
intent. Slash text, buttons, cards, and other product grammars may invoke the
same typed action; they do not become different Core semantics.

Channel adapters own native authentication, sender/Conversation identity,
access checks, transport acknowledgement, media staging, native encoding, and
native delivery truth. They do not import Gateway implementation or concrete
Application adapters.

### Gateway

Gateway is the single bridge runtime and public composition root. It owns:

- inbound admission, stable claim fencing, and bounded capacity;
- Conversation serialization and hierarchical binding;
- cross-authority workflow coordination;
- one Thread observation worker per stable Thread in one Gateway runtime;
- projection routing, checkpoints, correlation, and recovery;
- bounded delivery planning, coordination, idempotency, and receipts;
- bridge-state persistence, lifecycle, and redacted diagnostics.

Gateway does not own Agent resources, transcript, Turn/request/execution truth,
product commands, default CWDs, native UI state, Channel credentials, or
artifact bytes.

### Applications

Applications own Project/Workspace, Thread, Turn, request, transcript, native
input acceptance, interruption, and execution truth. Concrete adapters map
typed resource queries and operations to evidenced native behavior and
normalize native history/events into canonical values.

An adapter never exposes raw native events to Gateway consumers, guesses an
unsupported protocol method, or represents live-only presentation as
recoverable history.

## Public composition

The v1 Python experience has one explicit object graph:

```python
store = SQLiteGatewayStore(path="bridge.sqlite3")

commands = CommandRegistry(limits=CommandLimits(...))
include_common_commands(commands, names=(...))
commands.register(product_command)
commands.freeze()

gateway = Gateway(
    gateway_id="primary",
    channels=[channel],
    applications=[application],
    store=store,
    controller=commands,
    projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
    limits=GatewayLimits(...),
    extensions=GatewayExtensions(...),
)

async with gateway:
    await gateway.wait_closed()
```

`GatewayStore` is a typed public persistence port;
`MemoryGatewayStore` and `SQLiteGatewayStore` are the two built-in v1 choices.
A consumer configures one coherent store, not a loose service-locator
dictionary of repositories or a mixture of durable and process-local owners.
Additional store implementations must pass the same conformance suite.
Internally the store presents focused typed repositories and transaction
boundaries to their owners. It persists only bridge state:
bindings, routes, checkpoints, correlations, idempotency, and minimal Gateway,
workflow, and native-action receipts. It never persists transcript content,
native execution state, or artifact bytes.

One `gateway_id`/store namespace has one active Gateway runtime. `GatewayStore`
therefore provides an exclusive, renewable runtime lease with an opaque owner
token and monotonically increasing fencing epoch. Startup acquires the lease
before restoring workers or accepting input. Every mutating transaction
verifies the owner token, fencing epoch, and unexpired lease in the same store
transaction, using store-authoritative time rather than the caller's clock.
Lease loss closes admission and prevents stale workflow, binding, idempotency,
checkpoint, and delivery mutations before workers are stopped. Memory
implements the same rule in process; SQLite and custom stores must prove crash
expiry and stale-owner rejection. This lease prevents two Gateway runtimes
from turning one store into duplicate observation/delivery authority; it is
not a general active-active scheduler and does not revoke an external native
call that an old owner already fenced.

`Gateway.start()`/`stop()` support embedding. The async context manager is the
preferred lifecycle. A `run()` convenience may wrap the same object and same
lifecycle; it may not create another runtime or authority.

Composition is immutable after startup. Invalid capabilities, duplicate
Application/Channel identity, command collisions, unfrozen registries, invalid
limits, and incompatible store configuration fail before input is accepted.

## Consumer action surface

All product interaction paths use one Conversation-scoped action surface:

```python
actions = gateway.actions(conversation_ref, actor=authenticated_actor)
```

A Controller handler receives that same `ConversationActions` instance. It is
already scoped and cannot mutate another Conversation. Non-text native UI
actions obtain it from Gateway using the authenticated `ConversationRef`;
they do not manufacture an `InboundMessage` or Slash command.

The actor is authenticated/admitted by the Channel or consumer before the
surface is created. Gateway scope validation does not invent product
authorization, but Conversation identity alone is never treated as proof that
an arbitrary caller may act.

Trusted in-process consumer code may also obtain
`gateway.application(application_ref, principal=operator_principal)`, a typed
`ApplicationActions` surface for native resource reads and mutations that
intentionally have no Conversation effect. The stable authenticated principal
is required for action identity and audit scope; the SDK does not infer its
authorization. The surface uses the same operation engine as Conversation
workflows and never exposes the concrete adapter. `ConversationActions`
composes that surface internally; it is not a second Application path.

The v1 methods are grouped by semantic authority. Each method constructs and
executes a closed typed Operation variant. The methods are the ergonomic Python
surface, not an alternative free-form RPC; language-neutral schemas define the
same intent and result families.

### Discovery and reading

- `list_applications`
- `get_application`
- `list_projects`
- `get_project`
- `list_threads`
- `get_thread`
- `get_thread_status`
- `read_history`
- `read_turn_catchup`
- `get_binding`

Reads never mutate binding, observation, native UI activation, or resource
selection. Pagination and all returned collections are bounded.

### Native Application mutations

- `create_project`
- `create_thread`
- `activate_native_thread`
- `delete_project`
- `delete_thread`
- `interrupt_turn`

These operations affect Application truth only. `create_project` does not
select; `create_thread` does not bind or observe; activation does not change IM
input or output routing; deletion does not silently clear Conversation
bindings. A binding whose native ancestor was deleted becomes explicitly stale
on resolution. Unsupported capability is a normal typed result, and destructive
actions remain subject to consumer authorization and confirmation policy.

### Gateway mutations

- `select_application`
- `select_project`
- `bind_thread`
- `clear_thread`
- `clear_project`
- `clear_application`
- `observe_thread`
- `clear_observation`
- `respond_request`

Bindings are hierarchical:

- selecting an Application clears Project and Thread;
- selecting a Project selects its Application and clears Thread;
- binding a Thread selects all of its ancestors;
- clearing Thread retains its Project;
- clearing Project retains its Application;
- clearing Application leaves the Conversation unbound.

Observation selects output destinations and never selects input or activates
native UI state. Under `foreground_only`, binding prepares the matching route
inside the same Gateway-store transaction and binding equality is its output
authority; explicit clear-observation cannot silence a still-bound foreground
route. Other projection policies require explicit observation and permit
explicit route removal.

The public Conversation `respond_request` action first validates that this
Conversation received the request and that its bounded response shape matches;
only then does Gateway invoke the Application-owned native response mutation.
The raw native mutation is not exposed to a Controller because request ID
knowledge alone is not response authority.

### Safe cross-authority workflows

- `create_and_select_project`
- `create_and_bind_thread`

These are SDK workflows, not product policy. They exist because every consumer
would otherwise reproduce the same fencing, idempotency, partial-success, and
recovery rules. A product still decides when to call them and with which CWD,
Application, title, or initial context.

Handlers and native UI integrations must not hand-code these workflows by
sequencing primitive operations themselves.

## Action identity and results

Every effectful call requires a caller-stable `action_id`. For inbound
Controller work, the registry derives it from the stable Channel,
Conversation, message, canonical command/action, and bounded arguments. For
other callers, the consumer supplies it from a stable native request identity.

The effective identity is namespaced by `gateway_id`, trusted actor/principal,
Conversation when applicable, action kind, and caller action ID. The SDK then
derives domain-separated child operation IDs for workflow phases. Reusing an
effective ID with a different semantic payload is a typed conflict. Paths,
titles, text, or time never substitute for identity. Persistence retains a
bounded fingerprint, not the CWD, title, content, credential, or arguments
used to compute it.

Every store-only Gateway mutation (`select`, `bind`, `clear`, `observe`, and
`clear_observation`) uses a durable Gateway-action receipt. In one lease-fenced
store transaction it checks capacity and fingerprint, applies the binding or
route mutation and any generation change, and commits the terminal result.
Same-ID retry reads a terminal receipt before current state; changed payload is
conflict. If transaction acknowledgement was lost, the atomic receipt
distinguishes committed success from proven absence, so an old retry never
reapplies a selection or clear over newer user intent.

Every native Application mutation, including primitive `create`, `delete`,
`activate`, and `interrupt` plus Conversation-scoped `respond_request`, uses a
durable native-action receipt. Gateway persists the fingerprint and
`native_side_effect_started` fence before the adapter call. A known result
becomes terminal; cancellation, timeout, crash, or lost response after the
fence becomes sticky unknown unless the native capability proves idempotency
or terminal operation-status reconciliation by that stable action ID. A normal
resource lookup, missing resource, or temporary `not_found` is never proof
that a paused old caller cannot still commit. Retrying the same ID returns or
safely reconciles its receipt; it never blindly repeats a native mutation.
Gateway, primitive native, request-response, and composite workflow receipts
share the same configured finite non-evicting effect-receipt capacity.

Runtime domain outcomes are returned as closed typed results, not hidden in a
Boolean:

- `succeeded(value)` — the authoritative result is known;
- `failed(error)` — the effect is proven absent or a terminal rejection is
  known;
- `partial(value, error)` — a composite workflow committed an earlier effect
  and a later effect failed;
- `outcome_unknown(error)` — a native side effect may have happened and is not
  safe to repeat.

Programmer/configuration violations may raise before work starts. Expected
unsupported, stale, conflict, capacity, rejection, partial, and unknown
outcomes remain typed values.

For `create_and_select_project` and `create_and_bind_thread`:

1. persist the action fingerprint, stable phase identities, current binding
   generation, and `reserved` state;
2. durably fence the receipt as `native_side_effect_started` immediately
   before invoking native create;
3. invoke native create with the stable phase operation ID;
4. after a known result, persist either proven failure or the authoritative
   created resource reference;
5. after cancellation, crash, timeout, or lost response in the fenced state,
   reconcile only by repeating an evidenced idempotent native phase ID or by a
   terminal native operation-status protocol; a resource lookup or negative/
   temporary `not_found` is insufficient, so otherwise persist/return sticky
   `outcome_unknown` and never repeat create;
6. in one fenced store transaction, compare-and-swap the recorded binding
   generation and commit the hierarchical binding, any matching
   `foreground_only` route, and the workflow receipt's terminal binding phase
   with its successor generation;
7. if commit acknowledgement or the caller response was lost, read the
   terminal workflow receipt first; its stored success remains authoritative
   even if a later user action has since changed the binding;
8. return success, stale/conflict, or partial with the created reference.

The SDK never deletes a created Project or Thread because binding failed.
Deletion may be unsupported or destructive and belongs to a separate explicit
intent. Repeating the same action may converge a known created resource and
retry only the uncommitted binding when its recorded binding precondition still
holds. An intervening Conversation selection makes the old workflow stale; it
cannot overwrite newer user intent. The SDK never blindly repeats an unknown
native create.

Every Conversation has a durable monotonically increasing binding generation,
including while it is unbound. Select, bind, and every clear increment it;
clear does not delete or reset the generation. Restart, row recreation,
compaction, and selecting the same resource never reuse an earlier generation.
Workflow CAS uses that generation, preventing clear/recreate ABA from reviving
an old action.

Effect receipts use configured finite, non-evicting capacity per `gateway_id`
namespace. Saturation rejects any new effectful public action before a native
or Gateway side effect. Completed, partial, and unknown receipts remain for the
namespace lifetime by default; time-based eviction is not safe idempotency.
Explicit purge or namespace rotation is an operator/consumer action that
requires an external guarantee that retired action IDs cannot be submitted
again. Tombstones count toward the same bound and cannot manufacture safe
replay after evidence is discarded.

## Normal input path

The only ordinary-message path is:

```text
Channel authentication and normalization
  -> durable stable-ID admission before media work
  -> optional Controller
  -> exact complete hierarchical binding or typed pre-acceptance failure
  -> authoritative bound Project and Thread existence preflight
  -> optional typed content transformation
  -> Thread observation established before dispatch
  -> prefer-active-Turn native input
  -> authoritative Application events/history
  -> one Thread observation worker
  -> current projection destinations
  -> destination presentation
  -> bounded delivery planning and coordination
  -> typed Channel receipt
```

An unbound message produces an explicit missing-binding outcome. A complete
binding whose Application is no longer registered or whose authoritative
Project or Thread no longer exists produces an explicit stale-binding outcome.
Both finish before content transformation, route/worker mutation, or native
input. The SDK does not choose a default Application/CWD, auto-create resources,
ask a product question, or queue input. A consumer may implement an onboarding
flow in its Controller using the same typed actions. Absence of a Controller
leaves ordinary input behavior unchanged.

A Controller decision is explicitly pass-through or consumed. An onboarding
Controller may pass the original message through only after its binding
workflow succeeded. A partial or unknown workflow must be consumed/presented
or allowed to reach the explicit missing-binding result; it cannot dispatch
ordinary input against guessed resource state.

A successful scoped action that changes a binding or projection route, including
a terminal receipt replay, crosses the projection runtime's explicit
reconciliation seam. Reconciliation reads current route/binding authority, so
an old replay cannot restore a later-removed route. A durable route success plus
a failed process-local activation is reported as `partial`, never false success;
the same action replay may converge activation without repeating its durable or
native mutation. Cancellation after terminal receipt joins the one activation
attempt; an incomplete baseline remains fenced until explicit replay converges.

The default continuation preference is `prefer_active_turn`. An adapter that
can steer returns `steered`; one that cannot returns `started`. It never
pretends to steer or silently queues.

## Controller and commands

There is one explicit local `CommandRegistry` per Gateway composition. It is
fully populated and frozen before startup. Registration is never global or
import-time. Command and alias collisions fail startup.

Handlers receive a typed invocation, the Conversation-scoped actions, and
constructor-injected product services. They receive no Gateway, repository,
claim, checkpoint, adapter client, credentials, `Any` context, or service
locator.

The registry bounds command/alias count, argument count and size, output size,
active handlers, lifetime, cancellation join, and diagnostics. An effectful
handler crosses the durable inbound effect fence before it runs. Handler or
presentation failure after that fence cannot authorize repetition.

SDK common commands and product commands use the same registry and are
included explicitly. Product-specific controls such as credits, model,
profile, Full Access, permissions, aliases, and wording remain product code.

## Projection and recovery

One Conversation binds at most one current Thread. One Thread may project to
many Conversations. One Gateway runtime owns at most one observation worker
per stable Thread and fans out normalized authoritative events.

Projection state is per destination. Stable authoritative item ID and stable
delivery ID drive idempotency; opaque IDs are never sorted or inferred from
time. Live subscriptions are established before bounded history reconciliation.
New routes use a bounded baseline; existing routes reconcile toward their
checkpoint under strict limits.

Scoped actions that make routes visible use the projection owner's opaque,
generation-specific barrier lease. Same-route action lifecycles serialize;
completion can affect only the generation it began, delivery rechecks the
current fence under the route lock, and removing a route retires that fence and
wakes its waiters. The lease conveys no repository or runtime authority and is
not exposed through a Controller consumer surface.

That lease is also scoped to one process-local projection lifecycle generation.
Shutdown closes route activation before worker cancellation. Reconciliation
must retain the same generation and a live worker through bounded baseline
completion. Shutdown and the post-preflight atomic route commit acquire one
composition-owned lifecycle fence: a shutdown winner produces explicit failure
with no receipt or route write, while a commit winner completes before shutdown
and may become partial during activation. A foreground create-and-bind workflow
enters the same fence after its native result is durably known: a shutdown
winner preserves that created reference as partial but writes no binding/route,
and restart resumes without another native call. Restart and terminal replay
converge the same route without another mutation or dispatch path.

Recoverable output must be reproducible from Application history. Live-only
output does not advance a completion checkpoint. Missing/expired replay or
checkpoint evidence produces explicit degraded health rather than an SDK event
journal or unbounded scan.

The SQLite conformance path reconstructs a new Gateway and store instance
while reusing only an Application whose authoritative history survives. A
stop/start of the same in-memory objects is useful smoke coverage but is not
sufficient recovery proof.

## Requests, media, and artifacts

Interactive requests remain Application truth. Gateway persists only minimal
per-destination response-routing evidence after delivery; a Conversation that
did not receive a request cannot respond. Product authorization remains
outside that correlation check.

Attachment sources are typed. A message never grants filesystem or network
trust. Channel staging and Application materialization use configured bounds
and trusted roots/policies.

Artifact bytes, paths, quota, leases, cleanup ledger, crash-safe cleanup, and
startup sweep belong to the consumer. Adapter presentation/materialization may
receive bounded typed facts but cannot create a second native subscription or
expose raw protocol events.

## Extension rule

There is no generic middleware pipeline, stage enum callback, raw native event
hook, mutable context bag, or service locator. An extension exists only at a
named typed position with one effect contract, bounded admission/lifetime,
stable identity, explicit replay/failure semantics, and an absent-path test.

Consumer-specific behavior remains in the consumer until evidence from two
real integrations or a concrete counterexample justifies common semantics.

## Failure discipline

All capacity is finite, identity is stable, and failure is explicit.

- Known pre-side-effect failure may be reclaimed according to inbound policy.
- Unknown native mutation is sticky and is never automatically retried.
- Post-acceptance failure cannot repeat input.
- Unknown Channel delivery is never silently resent.
- Partial delivery preserves accepted prefix/item evidence.
- One destination failure does not restart Application observation.
- One observer overflow produces an explicit gap and authoritative recovery;
  it does not block native socket input or create an event spool.
- Presenter, extension, or Channel failure never grants Application mutation
  authority.

## Diagnostics and lifecycle

`gateway.diagnostics()` returns a synchronous immutable, bounded, redacted,
process-local snapshot with no I/O. It is not authoritative health, an event
log, an exporter, or an operator UI. Consumers translate it into their own
observability system.

Startup validates composition, opens bounded admission, restores bridge
workers, starts Applications and Channels, and drains claimed startup input in
a defined order. Rollback and stop close admission first, cancel and join all
owned bounded work, stop Channels, and stop Applications. Late callbacks
cannot enter a stopping Application.

## Repository transformation rule

The repository is transformed toward this design, not the reverse:

1. write or update authority before changing behavior;
2. keep one canonical runtime and one public API;
3. delete conflicting old facades, Project-less branches, compatibility shims,
   duplicate examples, and duplicate ownership;
4. move existing safety algorithms and tests to their correct owners;
5. require every vertical block to update the executable v1 specification;
6. judge completion from a clean installed wheel and the full consumer flow,
   not from file count, an isolated layer, or a passing import test.

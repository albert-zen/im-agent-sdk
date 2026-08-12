# IM Agent SDK v1 executable specification

Status: normative acceptance plan for `docs/V1_DESIGN.md`

This specification defines the one neutral executable consumer that proves the
v1 architecture. It is a design and acceptance artifact first. Existing
example code is reused only when it matches the scenarios below.

## Artifact shape

The repository keeps one product-neutral consumer:

```text
examples/reference_consumer/
  interaction.py   # deterministic Channel and local Controller composition
  application.py   # deterministic managed and fixed/flat Application evidence
  gateway.py       # one explicit production-shaped Gateway composition
  main.py          # executable golden path and concise report
```

The example imports only installed public SDK surfaces. Test-only assertions
may inspect deterministic fake counters, but the scenario may not call a fake
resource mutation, native event emitter, repository, or internal Gateway
method to bypass the public consumer path.

The example is executed from the source tree during development and from a
clean installed wheel before release.

## Target consumer usage

The reference `main.py` is shaped like the following. SDK names imported from
`imagent` are the v1 public contract; the other names are example-owned local
helpers. Implementation work changes the repository to make this usage real
rather than changing the example to fit pre-v1 internals.

```python
from imagent import (
    CommandLimits,
    CommandRegistry,
    Gateway,
    GatewayLimits,
    MemoryGatewayStore,
    ProjectionPolicy,
    Succeeded,
)


async def run_reference_consumer() -> None:
    commands = CommandRegistry(limits=CommandLimits(...))
    include_common_commands(commands, names=(...))
    commands.register(build_status_command(status_service))
    commands.freeze()

    gateway = Gateway(
        gateway_id="reference",
        channels=[channel],
        applications=[application],
        store=MemoryGatewayStore(),
        controller=commands,
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        limits=GatewayLimits(...),
    )

    async with gateway:
        actions = gateway.actions(
            conversation.ref,
            actor=conversation.authenticated_actor,
        )
        project_result = await actions.create_and_select_project(
            application.ref,
            cwd=reference_workspace,
            action_id="reference:create-project:1",
        )
        if not isinstance(project_result, Succeeded):
            raise ReferenceScenarioFailed(project_result)

        thread_result = await actions.create_and_bind_thread(
            project_result.value.ref,
            title="reference thread",
            action_id="reference:create-thread:1",
        )
        if not isinstance(thread_result, Succeeded):
            raise ReferenceScenarioFailed(thread_result)

        await channel.receive(
            conversation.text_message(
                message_id="reference:message:1",
                text="Reply with the reference acknowledgement.",
            )
        )
        await conversation.wait_for_delivery()
```

`channel.receive` and the Conversation helpers above are the deterministic
reference Channel's native ingress/test surface, not Gateway back doors. The
ordinary message still crosses authentication, normalization, durable
admission, Controller pass-through, binding, native Application input,
authoritative observation, and Channel delivery. No example is accepted if it
calls a Gateway internal, a repository, or a fake Application mutation to make
the scenario pass.

## Minimal golden path

The default run performs this sequence:

1. Construct one managed Application, one deterministic Channel, one
   `SQLiteGatewayStore`, one frozen registry containing selected common
   commands and one neutral product command, and one Gateway.
2. Start Gateway through its async context manager.
3. Conversation A discovers the Application and calls
   `create_and_select_project` with an explicit CWD.
4. Conversation A calls `create_and_bind_thread` in that Project.
5. Conversation A sends ordinary text through Channel ingress. The text passes
   the Controller, reaches the bound Thread, and the authoritative Agent reply
   returns through Channel delivery.
6. Conversation B selects the same Project and binds the same Thread.
7. One authoritative Thread output is delivered to A and B while the
   Application reports one active observation worker/subscription.
8. A creates and binds Thread 2. Output from Thread 1 now reaches only B;
   output from Thread 2 reaches only A.
9. A binds Thread 1 again. New Thread-1 output reaches A and B without a second
   Thread-1 worker or duplicate delivery of completed items.
10. Read redacted diagnostics and stop Gateway from the owned lifecycle,
    closing the original Channel, registry work, lease/session, and store.
11. Through the example Application's native ingress, complete one stable item
    while the Gateway is absent. Retain only that Application and its bounded
    authoritative history.
12. Construct fresh Gateway, Channel, registry, and SQLite store objects over
    the same database. Startup reconstructs both bindings, independent routes
    and checkpoints, completed input/output idempotency records, and terminal
    workflow receipts with one Thread worker. Public binding generations remain
    unchanged across reconstruction and each active destination checkpoint
    advances only from its prior stable item to the missed stable item.
13. Reconcile the missed item once to A and B without older-item duplication or
    native-input redispatch, replay the stable Project/Thread workflow results
    without another native call, and resend a prior stable Channel input identity
    without another Application call or delivery. Then close every fresh runtime
    object.
14. Inspect one consistent WAL-aware read-only database snapshot and every
    present SQLite sidecar; fail if the exact `sqlite_schema` object set or
    normalized table/index/trigger definitions, column/type schema, bounded
    deterministic row shape, bounded JSON values, or bridge-state invariants
    differ. Bounded descriptor reads also reject exact, UTF-16, base64, hex,
    and common compressed forms of transcript, native payload, request body,
    media, artifact, credential, or workspace-path sentinels. The bounded
    sidecar set, entry kind, path identity, size, and timestamps must remain
    stable across inspection; appearance, disappearance, replacement, or a
    non-file sidecar fails closed.

The executable prints one bounded summary only after all assertions pass.

## Command and policy path

The same run proves:

- the registry is local and frozen;
- one selected SDK common command executes;
- one neutral read-only product command uses a constructor-injected typed
  service;
- one neutral effectful command calls a Conversation workflow action after the
  durable command fence;
- common `/new` establishes live projection through the scoped route action, and
  later authoritative Thread output is delivered even though the command input
  itself was consumed;
- terminal route-action replay reconciles current live observation again, while
  activation failure is a typed partial result rather than success, and
  post-receipt cancellation/baseline failure cannot open an unreconciled route;
  same-route action generations serialize, stale completion cannot open a newer
  fence, route removal retires blocked delivery before same-ID re-add, and
  shutdown/worker termination cannot produce false activation success; one
  lifecycle fence gives shutdown or the atomic route commit a deterministic
  winner after authoritative preflight, and lifecycle restart replay converges
  any durable route; public Memory and SQLite composition prove the same fence
  prevents a known foreground Thread workflow from binding/routing after a
  shutdown winner and resumes it without another native create;
- duplicate registration and an unfrozen registry fail before Gateway accepts
  input; and
- with no Controller, Slash-looking text is ordinary Agent input.

The example does not choose a product default CWD, auto-create on first input,
or include product-specific commands.

## Capability honesty path

Focused tests compose three Project modes:

- managed Application: multiple stable Projects, CWD creation advertised and
  implemented idempotently;
- fixed Application: one configured workspace Project, list/get supported,
  create and native switch unsupported;
- flat Application: one stable adapter workspace Project, list/get supported,
  native project management unsupported.

Every returned Thread has a `ProjectRef`. The fixed/flat scopes are stable
across restart and never advertise native management. Reusing a workspace ID
with a changed canonical-root fingerprint fails startup; assigning a new ID
makes the old binding stale.

Tests prove that capability preflight and runtime results agree, unsupported
does not mutate the configured workspace, and no adapter guesses a native
endpoint or command.

## Cross-authority outcome path

For both create-and-select Project and create-and-bind Thread, focused tests
prove:

- success commits native resource plus Gateway binding;
- native rejection leaves binding unchanged;
- native unknown outcome does not attempt binding or repeat creation;
- a crash/cancellation after the durable native fence and before result
  persistence becomes sticky unknown unless evidenced native reconciliation
  returns the authoritative result;
- binding failure returns partial with the authoritative created resource;
- no automatic delete/compensation occurs;
- binding, its matching `foreground_only` route, successor generation, and
  terminal workflow receipt commit in one fenced transaction;
- a fresh SQLite process after commit-but-before-response returns the stored
  success for the old action even when the user subsequently bound a different
  Thread, without changing that newer binding;
- retry with the same action ID and payload converges the existing resource and
  uncommitted binding phase only while its recorded binding generation remains
  current;
- an intervening newer binding makes the old partial workflow stale rather
  than overwriting current Conversation intent;
- select → clear and select → clear → reselect never reuse a Conversation
  binding generation, so an old unbound precondition cannot pass by ABA;
- a fresh SQLite/store process after clear, compaction or unbound-row
  recreation preserves the next generation; a workflow with a known native
  result and an old uncommitted binding precondition remains stale rather than
  passing CAS;
- retry with a changed payload is a conflict; and
- cancellation at every await is classified by the actual side-effect fence;
- workflow capacity saturation fails before effects, receipts do not expire by
  time, and explicit namespace rotation requires retired-ID guarantees.

## Store-only Gateway action path

Every `select`, `bind`, `clear`, `observe`, and `clear_observation` action is
tested through its public Conversation surface:

- capacity reservation, payload fingerprint, state mutation, generation, and
  terminal result commit in one lease-fenced store transaction;
- changed payload with the same action ID is conflict;
- fresh SQLite restart after commit-but-before-ack returns the terminal receipt
  before inspecting current state; and
- after a newer action changes the binding or route, retrying the older
  terminal action returns its stored result and does not reapply its mutation.

The public action-to-store matrix also proves that `foreground_only` primitive
Thread binding commits its matching route with the binding, guarded
clear-observation preserves that route while the binding still matches, and a
fresh guarded clear removes it after the Conversation becomes unbound. Other
projection policies neither manufacture a route during binding nor protect an
explicit route from removal.

The matrix covers every store-only Gateway mutation listed in
`docs/V1_DESIGN.md`, not only Project/Thread selection.

## Primitive native action path

Every direct `ApplicationActions` mutation and Conversation-scoped
`respond_request` is tested with the same durable effect discipline:

- its stable action ID and payload fingerprint are reserved before the native
  side-effect fence;
- response loss and restart after the fence return the stored terminal result
  or sticky unknown and never blindly repeat the adapter call;
- reconciliation is allowed only through evidenced native idempotency or a
  terminal operation-status protocol for that action ID;
- resource absence and temporary `not_found` are not proven-absent outcomes;
- same ID plus changed payload is conflict; and
- primitive receipts consume the same finite non-evicting capacity as
  workflow receipts, with saturation before effects.

The matrix covers create Project, create Thread, activate Thread, delete
Project, delete Thread, interrupt Turn, and request response, including
adapters without native idempotency. The request-response case proves recipient
correlation before the durable native fence, native acceptance followed by
lost acknowledgement, fresh SQLite restart, and no duplicate adapter call.

## Scoped action boundary path

Public-surface, typing, and runtime-negative tests prove:

- `ApplicationActions` construction requires a stable authenticated principal
  and never exposes a concrete adapter;
- it has no Conversation binding, observation, or request-response authority;
- `ConversationActions` is frozen to its authenticated Conversation and actor,
  with no parameter that can substitute another Conversation;
- request response is available only through the Conversation surface that
  received the request; and
- neither surface exposes Gateway, store, repository, claim, checkpoint,
  credential, native client, or `Any` context.

## Ordinary input safety path

Focused tests enter only through Channel ingress and prove:

- unbound input fails explicitly and creates no Application resource;
- stable duplicate message identity is rejected before media work;
- the optional Controller runs off the Channel socket path;
- Controller-unconsumed input preserves envelope identity through any content
  transformation;
- observation is established before native dispatch;
- a native Thread that is valid immediately after creation but has no history
  resource yet can bind in the foreground and accept its first ordinary input;
  its adapter supplies only the exact empty pre-input baseline and does not
  request turn-bearing continuation state before that input, then retires
  that evidence at the native dispatch fence so the first live output is
  delivered once and later observation uses normal authoritative history;
- the first-input start plan holds the exact create-evidence generation, not a
  native Thread snapshot; after the asynchronous dispatch fence it revalidates
  current connection epoch, native session, authorized revision, and evidence
  identity immediately before `turn/start`, failing without native mutation
  when a reset/reconnect, allowlisted Turn notification/request, or same-ID
  replacement invalidates the plan, and identity-specific retirement cannot
  delete newer same-ID evidence;
- new-Thread evidence is bound to the current native connection epoch, stable
  native session identity, and finite revisions authorized only by create or
  allowlisted non-Turn initialization facts; stop, reset, foreign activity or
  a non-authorized revision change,
  same-ID recreation, or session change restores strict history and active-
  Turn discovery;
- a delayed partial `thread/started` notification after create-evidence
  installation cannot retire that evidence from its payload; the create
  generation lock plus a no-turn authoritative scope read may authorize one
  bounded all-equal creation-clock revision family only after the first
  lifecycle event proves exact native identity continuity, while a
  non-creation revision change, scope failure, reset, foreign drift,
  and real same-ID recreation retire only the captured generation and a stale
  prior-epoch event cannot erase its successor;
- an allowlisted Turn event racing a valid in-flight empty baseline retires
  later eligibility but does not invalidate that captured baseline; the live
  event drains once after the bootstrap barrier, while hostile thread/unknown
  methods carrying an extraneous Turn ID do not retire evidence;
- `prefer_active_turn` produces truthful `started` or `steered` acceptance;
- known pre-dispatch failure, unknown dispatch outcome, and post-acceptance
  failure retain their distinct claim states; and
- presenter or delivery failure never repeats native input.

## Projection and restart path

The in-memory run proves routing and lifecycle quickly. A separate executable
test uses `SQLiteGatewayStore`:

1. start Gateway instance 1 and complete the managed Project/Thread binding;
2. deliver at least one completed authoritative item;
3. stop and discard Gateway and store objects;
4. create authoritative Application output while Gateway is absent;
5. construct Gateway instance 2 from the same SQLite database and the same
   Application authority;
6. verify binding/route restoration, subscribe-before-history recovery, one
   worker per Thread, delivery of the missed item, and suppression of already
   completed items.

The database is inspected to ensure it contains no message body, transcript,
Turn/request truth, artifact bytes, raw native event, credential, or untrusted
path.

Gap, cursor expiry, missing checkpoint, queue overflow, and Channel failure
have explicit health/result assertions and finite bounds.

The App Server counterexample additionally proves that create plus foreground
bind preserves subscribe-before-baseline when `thread/turns/list` is invalid
until first input. Exact bounded adapter create evidence permits only an empty
checkpoint-free baseline. Non-created Threads, stop/restart, native epoch,
session, or non-authorized revision change, same-ID reuse, evidence eviction
or adapter reconstruction,
checkpointed routes, restart recovery after the first dispatch fence, and
every unrelated history failure remain strict. Memory and SQLite
public Gateway compositions exercise the same route and lifecycle behavior.

## Request, media, and artifact path

These are focused conformance scenarios rather than noise in the minimal text
run:

- a request is delivered to two Conversations, one authorized response wins
  in the Application, and a non-recipient cannot respond;
- reconnect without native pending-request evidence becomes stale rather than
  manufactured recovery;
- attachment source support, scalar types, immutable metadata snapshots, and
  trust are preflighted before native side effects; rooted `LocalPath` bytes
  are acquired through a no-follow descriptor chain so the bytes hashed are
  the bytes submitted even if the pathname is swapped; every SDK-owned native
  Channel and T3 exercise this boundary, while App Server path-only image input
  is explicitly unsupported before native dispatch;
- recoverable presentation is identical in live and history normalization;
- live-only presentation never advances a completion checkpoint; and
- artifact materialization uses a consumer-owned bounded, fsync-backed ledger
  whose startup read rejects ledger symlinks, replacements, and growth, plus a
  finite startup sweep while the SDK stores no bytes or durable spool;
  retryable destinations retain their lease through the explicit retry; and
- Memory and SQLite terminal proactive replay precede credential
  reauthorization, including revocation and same-token principal rotation,
  and perform no second native send.

## Lifecycle and diagnostics path

Tests prove startup validation, partial-start rollback, startup-buffer
overflow, late-callback rejection, normal shutdown, cancellation join bounds,
and repeatable construction of a fresh Gateway instance. Concurrent startup of
two Gateways in one store namespace proves exclusive lease acquisition,
monotonic fencing, crash expiry, and rejection of a mutation carrying an old
owner token, old epoch, or expired lease.

The owner deadline is monotonic and remains hard through repeated caller
cancellation; synchronous shutdown fences are classified without skipping later
owners. Admission rollback records owner/type-only cleanup evidence. Diagnostic
normalization rejects hostile numeric subclasses, maps every malformed
projection record to fixed degraded/`other` facts, and preserves both bounded
Application queues where the adapter contract advertises them.

A takeover test pauses owner A after `native_side_effect_started`, expires its
lease using store-authoritative time, and starts owner B. A negative native
lookup or temporary `not_found` leaves B sticky unknown; B does not retry. When
A resumes, its stale store mutation is rejected. A separate evidenced-native-
idempotency case may converge both callers under the same phase ID without a
duplicate resource.

Diagnostics assertions cover only allowlisted aggregate facts. Thread,
Conversation, request, route, content, path, endpoint, credential, native
message identity, and free-form exception text never appear.

## Downstream experimental acceptance path

After the SDK candidate satisfies every in-repository gate, the final delivery
includes three isolated downstream rewrites: IMCodex, IMT3, and IMZen. Each is
implemented on its own experimental branch and worktree against one exact SDK
candidate commit or wheel. These rewrites are acceptance consumers, not design
authorities: product policy remains downstream, and no SDK abstraction is
accepted merely to preserve their pre-v1 structure.

Each rewrite must:

- compose only the installed public SDK surface, with no SDK private imports,
  copied Gateway runtime, or compatibility shim;
- retain downstream configuration, credentials, authorization, product
  commands, presentation, branding, and launch policy at the product layer;
- delete or bypass the duplicated IM-to-Agent bridge path in the experimental
  composition rather than running two authorities;
- prove explicit Application/Project/Thread selection, ordinary input and
  authoritative output, restart behavior, and applicable request/media paths;
- run its repository-native tests plus at least one real public-path vertical
  scenario; and
- record any generic SDK defect back in the SDK, fix and re-review the SDK
  candidate, then rerun all affected downstream scenarios.

The experimental branches are not merged to downstream default branches as
part of SDK automation without separate human approval. If a downstream
checkout is not attached to a real Git repository, repository provenance must
be resolved before creating its branch/worktree; automation must not silently
initialize or invent an upstream.

## Conformance ledger

Every vertical implementation PR updates this table and adds the named public
scenario in the same PR. A check is complete only when the source-tree and
clean-wheel executions both pass.

| Capability | Required executable evidence | State at design baseline |
|---|---|---|
| public composition and owned lifecycle | async context manager, pre-I/O validation, start/stop rollback with cleanup failures | implemented through H: one serialized explicit/context/run lifecycle, cancellation-resistant joined close, finite owner timeouts, body-primary cleanup evidence, lease-loss reporting, terminal reconstruction semantics, and exactly-once continuation |
| uniform Application → Project → Thread → Turn resources | managed/fixed/flat tests; every Thread has ProjectRef | implemented in DAG A |
| managed Project CWD creation | success, capability honesty, stable action identity | contract and deterministic fake evidence implemented in A; native support remains capability-gated |
| store-only Gateway mutation fencing | atomic terminal receipt; old same-ID retry never overwrites newer intent | B memory/SQLite executor parity, C scoped request mapping, and E public lifecycle wiring implemented |
| primitive native mutation fencing | lost ack/restart/unknown for create, activate, delete, interrupt, request response | B durable executor parity and C scoped callback mapping implemented; G wires delivered-destination request responses and native first-writer truth through the canonical public lifecycle |
| managed Project deletion | typed action, capability honesty, stale binding, durable native fence | B/C seam acceptance implemented; concrete native support remains capability-gated |
| Project create-and-select workflow | success/partial/unknown/conflict | B/C durable coordinator and scoped action mapping implemented |
| Thread create-and-bind workflow | success/partial/unknown/conflict | B/C durable coordinator and scoped action mapping implemented |
| scoped consumer actions | principal, Conversation isolation, no adapter/store escape | implemented in DAG C; D supplies the coherent-session Controller/action seam and commit fences; E exposes it through the canonical public Gateway factories |
| ordinary Channel → Agent → Channel text | no direct fake mutation | D's policy-free binding/dispatch and authoritative stale-binding preflight execute through the public native-ingress round trip in DAG E |
| multi-Conversation one-Thread fan-out | one worker, two destinations | public two-Conversation path and one-subscription counter implemented in DAG E |
| foreground switch and switch-back | route authority in both directions, no duplicates | D-fenced scoped actions drive both directions and exact destination isolation in DAG E |
| SQLite restart recovery | fresh Gateway/store objects, no SDK content truth | implemented in DAG F through the same installed public reference consumer: fresh Gateway/Channel/store objects restore binding, route, checkpoint, idempotency, and effect-receipt evidence from one SQLite database while only the authoritative Application survives; missed output reconciles once and database/sidecar inspection rejects content or native-authority leakage |
| local common and product commands | read-only plus effectful typed service/action | implemented in DAG C focused registry/action evidence |
| unsupported/stale/capacity/partial/unknown | typed consumer-visible outcomes | D covers input/binding failures; G adds explicit request stale/unauthorized/duplicate, proactive rejected/partial/unknown, sticky unknown replay, finite authorization/submission bounds, and side-effect-free media rejection |
| request response routing | recipient correlation and native first-writer truth | implemented in DAG G through canonical `ConversationActions`, authoritative pending-snapshot recovery, and explicit no-snapshot staleness |
| media/artifact boundaries | trust, bounds, consumer-owned bytes/cleanup | implemented in DAG G through public planning/delivery plus the injected reference artifact ledger and exact SQLite/WAL/sidecar non-persistence inspection; the same executable proves live/history presentation parity and no checkpoint advance for live-only output |
| diagnostics and graceful shutdown | redaction, finite cleanup, late callback rejection | implemented through H with fixed provider/cardinality/counter bounds, hostile fail-closed aggregation, every I1/I2/A1/O1/O2 and adapter owner, startup/worker/recovery/lifecycle facts, and cold/after-stop failure behavior |
| installed-wheel usability | same executable specification from clean wheel | implemented through H: one typed lazy facade, exact clean-process identities/import orders, metadata/marker/entry point inspection, and the same golden executable in all six isolated install profiles |
| downstream experimental rewrites | IMCodex, IMT3, IMZen isolated worktrees on exact SDK candidate | final acceptance only |

No pre-v1 test name, module boundary, or passing count is itself acceptance
evidence. Evidence is retained only when it proves the semantics in
`docs/V1_DESIGN.md` through the intended public path.

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
   `MemoryGatewayStore`, one frozen registry containing selected common
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
10. Read redacted diagnostics and stop Gateway from the owned lifecycle.

The executable prints one bounded summary only after all assertions pass.

## Command and policy path

The same run proves:

- the registry is local and frozen;
- one selected SDK common command executes;
- one neutral read-only product command uses a constructor-injected typed
  service;
- one neutral effectful command calls a Conversation workflow action after the
  durable command fence;
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

## Request, media, and artifact path

These are focused conformance scenarios rather than noise in the minimal text
run:

- a request is delivered to two Conversations, one authorized response wins
  in the Application, and a non-recipient cannot respond;
- reconnect without native pending-request evidence becomes stale rather than
  manufactured recovery;
- attachment source support and trust are preflighted before native side
  effects;
- recoverable presentation is identical in live and history normalization;
- live-only presentation never advances a completion checkpoint; and
- artifact materialization uses a consumer-owned bounded ledger and cleanup
  path while the SDK stores no bytes or durable spool.

## Lifecycle and diagnostics path

Tests prove startup validation, partial-start rollback, startup-buffer
overflow, late-callback rejection, normal shutdown, cancellation join bounds,
and repeatable construction of a fresh Gateway instance. Concurrent startup of
two Gateways in one store namespace proves exclusive lease acquisition,
monotonic fencing, crash expiry, and rejection of a mutation carrying an old
owner token, old epoch, or expired lease.

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
| public composition and owned lifecycle | async context manager, start/stop rollback | redesign required |
| uniform Application → Project → Thread → Turn resources | managed/fixed/flat tests; every Thread has ProjectRef | implemented in DAG A |
| managed Project CWD creation | success, capability honesty, stable action identity | contract and deterministic fake evidence implemented in A; native support remains capability-gated |
| store-only Gateway mutation fencing | atomic terminal receipt; old same-ID retry never overwrites newer intent | B memory/SQLite executor parity and C scoped request mapping implemented; public lifecycle wiring remains |
| primitive native mutation fencing | lost ack/restart/unknown for create, activate, delete, interrupt, request response | B durable executor parity and C scoped callback mapping implemented; public lifecycle wiring remains |
| managed Project deletion | typed action, capability honesty, stale binding, durable native fence | B/C seam acceptance implemented; concrete native support remains capability-gated |
| Project create-and-select workflow | success/partial/unknown/conflict | B/C durable coordinator and scoped action mapping implemented |
| Thread create-and-bind workflow | success/partial/unknown/conflict | B/C durable coordinator and scoped action mapping implemented |
| scoped consumer actions | principal, Conversation isolation, no adapter/store escape | implemented in DAG C; public composition factory integration follows coherent B store wiring |
| ordinary Channel → Agent → Channel text | no direct fake mutation | missing from reference flow |
| multi-Conversation one-Thread fan-out | one worker, two destinations | existing evidence requires public-path review |
| foreground switch and switch-back | route authority in both directions, no duplicates | partial existing evidence |
| SQLite restart recovery | fresh Gateway/store objects, no SDK content truth | missing |
| local common and product commands | read-only plus effectful typed service/action | implemented in DAG C focused registry/action evidence |
| unsupported/stale/capacity/partial/unknown | typed consumer-visible outcomes | incomplete |
| request response routing | recipient correlation and native first-writer truth | B replay-before-preflight and C authorized scoped action implemented; final lifecycle/projection integration remains |
| media/artifact boundaries | trust, bounds, consumer-owned bytes/cleanup | existing evidence requires integration |
| diagnostics and graceful shutdown | redaction, finite cleanup, late callback rejection | partial existing evidence |
| installed-wheel usability | same executable specification from clean wheel | DAG C public action/registry import and negative-export wheel gate passes; full golden executable remains |
| downstream experimental rewrites | IMCodex, IMT3, IMZen isolated worktrees on exact SDK candidate | final acceptance only |

No pre-v1 test name, module boundary, or passing count is itself acceptance
evidence. Evidence is retained only when it proves the semantics in
`docs/V1_DESIGN.md` through the intended public path.

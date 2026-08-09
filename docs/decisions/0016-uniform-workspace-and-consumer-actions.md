# ADR 0016: Uniform workspace resources and consumer action surface

Status: Accepted

## Context

Every supported Agent Application executes in a workspace/CWD context, but the
pre-v1 contract made `ProjectRef` optional for fixed and flat Applications.
That forced consumers to handle two resource trees and let Gateway infer
default Application/Thread creation during ordinary input. Product handlers
also had only primitive Application/Gateway executors, so recurring
create-and-bind workflows were reproduced outside their fencing and recovery
owner.

Managed, fixed, and flat Applications are real counterexamples to one native
Project-management API, but they are not counterexamples to a common workspace
scope. Codex/App Server configured CWD, T3 workspace discovery, and every
coding-Agent process provide a stable execution scope even when the native
protocol does not name it Project.

## Decision

### The public resource tree is uniform

Every public Thread belongs to one stable `ProjectRef` representing its
workspace scope.

- managed mode exposes native authoritative Projects and only advertises
  creation/deletion supported by evidenced native behavior; both have typed
  public actions;
- fixed mode exposes one stable configured workspace Project and rejects
  creation and other unsupported native Project mutations;
- flat mode exposes one stable adapter workspace Project and rejects native
  Project management.

The fixed/flat Project is an honest scoped projection of actual execution
context, not a claim that the native protocol implements Project management.
Mode and capabilities remain explicit. This decision supersedes ADR 0001's
Project-less flat/fixed representation.

Fixed/flat configuration supplies an immutable workspace ID. Gateway persists
a bounded fingerprint of its canonical execution root and rejects startup if
the same ID changes meaning. Intentional workspace replacement receives a new
ID so retained bindings become stale rather than targeting a different CWD.

### Gateway exposes scoped consumer actions

Product interactions use one `ConversationActions` surface scoped to an
authenticated `ConversationRef`. Trusted in-process resource administration
may use a typed `ApplicationActions` surface without exposing a concrete
adapter. Both use the same typed operation engine.

Primitive resource and binding actions remain semantically separate. The SDK
also owns two recurring cross-authority workflows:

- `create_and_select_project`;
- `create_and_bind_thread`.

Each effectful call has a caller-stable action ID. Store-only Gateway
mutations atomically commit their mutation, generation, and terminal receipt.
Every primitive native mutation, Conversation-scoped request response, and
composite workflow persists only a payload fingerprint, domain-separated phase
IDs where applicable, binding precondition, side-effect fence state,
authoritative result reference, and phase outcome. It stores no resource
content, transcript, native execution state, or product policy.

Creation plus binding is not represented as an atomic native transaction.
Results distinguish success, proven failure, partial success, and unknown
native outcome. A binding failure never triggers implicit native deletion. An
unknown native create is never automatically repeated.

The native side-effect fence is durable before create. Recovery may reconcile
that phase only through evidenced idempotency or a terminal operation-status
protocol by its stable phase ID. A normal resource lookup or negative/
temporary `not_found` cannot prove a paused old caller will not commit, so the
fenced result stays sticky unknown. The same rule applies to primitive native
mutations.

A later binding attempt uses the binding generation captured before create.
Conversation binding generation increases monotonically across select, bind,
clear, restart, and row recreation, including while unbound; it never resets
or reuses an earlier value. Binding, the matching `foreground_only` route, and
the workflow's terminal receipt phase commit in one fenced store transaction.
A stored terminal workflow receipt is read before current binding state, so a
later user selection cannot erase the old action's known result.

Gateway, primitive native, request-response, and workflow effect receipts
share finite non-evicting capacity. Saturation fails before effects; receipt
expiry is not used as retry permission. Purge/namespace rotation requires an
external guarantee that retired action IDs cannot return.

One store namespace also has one renewable, fenced Gateway runtime lease.
Every mutation checks owner token, fencing epoch, and lease freshness in its
store transaction using store-authoritative time. Stale owners cannot mutate
admission, workflow, binding, checkpoint, or delivery state after lease loss.
Lease expiry does not revoke an external native call already fenced by the old
owner.

### Ordinary input has no hidden onboarding policy

An unbound ordinary message produces an explicit missing-binding outcome.
Gateway does not silently select a sole Application, choose a CWD, create a
Project, create a Thread, or queue the message. Consumers may implement an
onboarding flow through their explicit Controller and the same typed actions.

## Classification

- **Core invariant:** uniform stable workspace scope, hierarchical binding,
  stable action/phase identity, truthful partial/unknown outcomes, and no
  implicit compensation.
- **Optional capability:** native Project discovery, creation, and deletion.
- **Adapter-specific behavior:** deriving the fixed/flat stable workspace
  identity and mapping native CWD/Project APIs.
- **Consumer policy:** default Application/CWD, automatic onboarding, command
  grammar, permissions, confirmation, and presentation.

## Consequences

`ThreadRef.project_ref` becomes required and binding validation always enforces
Application → Project → Thread ancestry. Fixed/flat adapters and tests must
expose their one honest scope. Existing Project-less code and schemas are
rewritten without a compatibility mode.

Gateway input dispatch becomes simpler and policy-free. Consumers gain a
small safe action surface instead of copying multi-step workflow logic.
Gateway bridge persistence gains minimal primitive and workflow effect
receipts but remains far smaller than a transcript or resource registry.

## Verification

The executable v1 specification proves managed/fixed/flat workspace behavior,
every Thread carrying a Project, explicit unbound input, both composite
workflow outcome families, primitive native-mutation fencing, stable
retry/conflict behavior, scoped action boundaries, and absence of automatic
delete or blind native retry.

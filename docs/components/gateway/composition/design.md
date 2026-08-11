# Gateway composition design

Component ID: `gateway.composition`

Parent: `gateway`

## Purpose

Composition assembles one explicit public `Gateway` object graph from typed
Applications, Channels, one coherent `GatewayStore`, limits, and optional
extension seams.
It is a wiring boundary, not a service locator or a fourth business layer.

## Ownership

This leaf owns the immutable `GatewayRepositories`, `GatewayLimits`, and
`GatewayExtensions` construction values and the wiring that passes each value
to its documented owner or read-only consumer. It does not own product
commands, native adapter internals, repository implementations, Channel access
policy, Application execution truth, or a generic pipeline/hook registry.

The configured binding repository is constructor-injected into the sole
mutating `gateway.routing.bindings` runtime. Projection-route policy receives
the same repository only as the read authority needed to test foreground
binding equality; `ImAgentGateway` itself does not retain or call the binding
repository. Composition still sequences Application Project/Thread truth,
binding transition facts, and foreground projection-route preparation without
becoming a second mutation owner.

Inputs are explicitly configured Application and Channel instances plus one
typed store, Controller, limit, and extension dependencies. The output is one
`Gateway` with no dependency lookup at runtime. Dependencies point to the
public Interaction and Applications
contracts and the specific Gateway admission, input, projection, presentation,
delivery, persistence, and diagnostics leaves that consume the values.
Coherent-session detection validates the complete structural capability so
both built-in Memory and deliberately delegated SQLite sessions wire the same
Controller/action graph; no session object crosses into a consumer surface.

The canonical public contracts are `Gateway`, `GatewayLimits`, and
`GatewayExtensions`; the built-in store choices implement the separate
`GatewayStore` port. `Gateway` lives in `imagent.gateway.runtime` and is an
exact lazy export from `imagent.gateway` and `imagent`. The construction values
live in `imagent.gateway.composition`. The removed
`imagent.gateway_composition` module has no compatibility shim.

At startup `Gateway` acquires exactly one lease-bound `GatewayStore` session.
Before that acquisition or any adapter/store I/O, the public composition
boundary validates every finite `GatewayLimits` field, each current
Application summary and capability contract, Channel and store structure, and
stable unique Channel/Application identities. Startup repeats the identity and
summary validation against the constructor snapshot so mutable adapters cannot
drift into dictionary-key replacement. The returned session must expose the
complete structural session contract and the exact requested Gateway/owner
lease before any runtime is constructed; malformed candidates are closed and
reported explicitly.
That same session supplies each focused internal bridge-state owner and creates
one `StoreBackedGatewayEffectExecutor`; scoped action factories receive only
the lifecycle-bound fenced `GatewayEffectExecutor` protocol. Composition
uses D's one exact action bootstrap lease and narrow commit fence before a new
route becomes visible, then reconciles the terminal store/workflow result
through the one SDK projection runtime rather than a consumer subscription. The
session, lease, concrete executor, repository views, and retiring
`GatewayRepositories` bundle never cross the canonical consumer surface.
Terminal replay remains authoritative: replay precedes lifecycle admission and
current worker capacity or inactive-route state cannot rewrite a stored
result. For new work, authoritative preflight remains outside the commit fence;
the atomic route transaction and projection shutdown take that fence in winner
order. After a successful commit, reconciliation stops workers no longer backed
by active routes before reserving capacity for the new Thread. Its cancellation
join and exact bootstrap lease ensure every holder completes or aborts without
opening a live-before-baseline window.

## Bounds and absence

`GatewayLimits` carries finite startup, recovery, projection (including active
Thread-observation), request, extension, in-memory idempotency, delivery-submission, and
Conversation-serialization limits. The immutable group validates every field
at construction, including positive integer capacities, positive finite
timeouts/retention, finite non-negative retry delays, and retry ordering. Leaf
runtimes retain their local defensive checks. The default
process-local idempotency repository receives its positive record bound only
from this group; an explicitly supplied repository remains unmodified. Invalid
finite-capacity configuration fails explicitly. `GatewayExtensions` contains
only the four Gateway-owned ADR 0015
typed positions plus the Controller/request presenter composition points; A1
stays Application-owned. The group is not an `Any` context, callback stage
enum, or service bag.

Every optional repository or extension has an explicit default. Omitting an
ADR 0015 extension preserves the pre-extension path exactly and creates no
synthetic callback, diagnostic invocation, persistence, or side effect. The
grouped constructor is the one supported construction shape; composition does
not preserve an unlimited parallel flat-keyword API.

The D ordinary-input integration accepts a Controller only when composition is
backed by one lease-bound `GatewayStoreSession` used as
bindings, projections, idempotency, request correlations, and delivery
submissions together. Every optional repository must be absent or that exact
object; mixing the session with any other repository fails during construction.
Gateway then constructs `StoreBackedGatewayEffectExecutor` and exposes only the
resulting `ConversationActions` to Controller code or scoped action values to
the public factories. This focused integration
also injects the projection runtime's public action-route bootstrap and
reconciliation methods as narrow callables into the private Controller action
adapter. Successful and terminally replayed route/binding actions converge
current observation without giving C or the Controller a repository/runtime
escape. The same typed seam rejects route activation outside the current
projection lifecycle generation: pre-write rejection is closed failure and a
durable route whose activation loses shutdown is closed partial, both without a
second dispatch or recovery path. It injects only an opaque async commit-fence
context into B: B retains the atomic store call, while projection stop and the
post-preflight transaction acquire the same process-local fence. Terminal
receipt replay remains before fence admission. Canonical `Gateway` owns the
public `GatewayStore` acquisition and deterministic release; the private
session and `GatewayRepositories` never become consumer-facing construction
ports.

`projection_max_active_threads` is passed only to the Thread observation
runtime. It bounds distinct stable `ThreadRef` workers in one Gateway process:
an existing, starting, or in-flight same-Thread admission joins before
admission, while a new Thread at capacity fails before its Application
subscription or projection work begins. The admission lease remains keyed to
that Thread through task turnover until the caller completes, so a distinct
Thread cannot consume the final slot after route preparation. The bound never
persists a slot or replaces route/checkpoint authority; terminal worker cleanup
discards worker-owned health, while accepted-input fence state remains owned by
the final input owner until it has safely drained.

## State and recovery

The construction values are frozen process configuration. The Gateway owns its
store lease, periodic renewal task, and deterministic release/close ordering;
renewal starts immediately after acquisition, including while inner startup is
still running. Renewal loss closes admission, invalidates all previously
issued scoped surfaces, stops the runtime, and closes the session/store before
`wait_closed()` reports the failure. Any failure after acquisition follows the
same owned cleanup path.
Durable recovery belongs to the configured store and runtime leaves.
Reconstructing an equivalent composition must not create a
second Application subscription, Channel admission path, transcript, Agent
runtime, content spool, or outbox.

## Current structure and remaining split

`src/imagent/gateway/runtime.py` hosts the lifecycle-owned canonical Gateway;
`src/imagent/gateway/composition.py` solely owns its three immutable
construction values. `src/imagent/gateway/controller_input.py` privately adapts
the exact Application, binding, D effect-executor, projection-reconciliation,
and route-commit-fence callbacks required by C for both public factories and
the optional inbound Controller. `src/imagent/gateway/__init__.py` retains the
single internal orchestration graph used by the canonical lifecycle; the
reference consumer never imports it or any private composition seam.

## Authority

- [Vision](../../../VISION.md)
- [Architecture](../../../ARCHITECTURE.md)
- [Gateway aggregate](../design.md)
- [ADR 0006](../../../decisions/0006-core-admission-and-policy-ownership.md)
- [ADR 0015](../../../decisions/0015-typed-extension-seams-and-composition.md)

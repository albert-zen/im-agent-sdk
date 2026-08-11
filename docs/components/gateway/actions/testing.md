# Scoped consumer actions testing

The focused owner mirror is `tests/gateway/test_actions.py`.

Required evidence:

- both surfaces are frozen and require authenticated scope facts;
- no Conversation method accepts a Conversation parameter;
- `ApplicationActions` exposes no binding, observation, workflow, or request
  response authority;
- neither public surface exposes implementation/state/credential objects or
  an `Any` context;
- reads are bounded and never call `GatewayEffectExecutor`;
- binding reads validate the complete hierarchy and reject a runtime value for
  any Conversation other than the surface's frozen Conversation;
- selection and observation preflight exact Application/Project/Thread
  existence and ancestry before store admission; Memory and SQLite evidence
  proves rejected resources cannot change a binding or route;
- store preflight writes a terminal rejection receipt and both accepted and
  rejected same-ID actions replay before later resource/capability drift;
- route-producing observe, foreground bind, and foreground create-and-bind
  reject unsupported streaming before a route or native Thread is created,
  while non-foreground binding remains valid;
- every successful or terminally replayed Conversation route/binding action
  invokes projection reconciliation; a runtime activation failure converts the
  durable success into typed `Partial` without repeating the store/native
  effect or exposing the runtime through the public surface;
- explicit observe and foreground bind install the projection owner's sole
  bootstrap barrier before the durable route write, release it for durable
  non-success or completed activation, and retain it across post-receipt
  activation failure;
- cancellation after terminal receipt is joined through reconciliation, and a
  failed baseline stays fenced until same-ID replay succeeds;
- overlapping same-route actions hold opaque generation-specific leases, so a
  failed, delayed, duplicate, or retired holder cannot release a successful
  replay's barrier or the action mutex for a re-added route;
- projection shutdown before a route write returns typed
  `Failed(stale_runtime)` with no route mutation, while stop racing a durable
  success or worker termination during baseline returns typed
  `Partial(stale_runtime)`; restart replay converges without repeating
  the durable action and leaves no worker, capacity, barrier, or lock leak;
- a terminal successful route action replays its original value while stopped
  as `Partial(stale_runtime)`, whereas a new action is the pre-write `Failed`
  case and changed payload remains conflict;
- a Channel delivery cancelled by shutdown remains sticky in-flight: restart
  does not resend it, action replay stays typed `Partial`, and the incomplete
  baseline fence cannot open or report false success;
- native/workflow preflight proves capability honesty before the native fence
  or callback, while a terminal receipt replays before changed capability or
  resource state is consulted;
- every primitive native mutation produces one `NativeMutationRequest` and no
  store mutation;
- every select/bind/clear/observe operation produces one closed
  `StoreMutationRequest` scoped to the fixed Conversation;
- hierarchical clears carry only `BindingClearScope`, never pre-read binding
  ancestors, and are accepted against B's replay-before-current-state tests;
- route-only plans carry that Conversation directly and have no synthetic
  binding target;
- `foreground_only` Thread binding atomically includes its matching route,
  while other policies do not; action-to-Memory/SQLite acceptance proves a
  foreground clear cannot remove that route until the Conversation is bound
  elsewhere;
- new CAS fields use `expected_generation` and no revision alias exists;
- both create-and-bind workflows produce one
  `CreateBindingWorkflowRequest`, preserve `Partial`, and never perform
  compensation deletion;
- `respond_request` is absent from `ApplicationActions`; its preflight rejects
  before the native fence while a terminal same-ID retry replays without
  reauthorizing a now-resolved correlation;
- bounded create payloads are rejected before snapshotting, fingerprinting, or
  executor admission; validated mutable context metadata and response-answer
  mappings are then snapshotted so caller mutation cannot change the later
  native invocation;
- user-input responses reject a 33rd question, 65th answer, or 4,097th answer
  character before traversal/copy/fingerprint/executor work, and accept each
  exact boundary;
- malformed Application or request-response success values never cross the
  public action-result boundary as success;
- same action ID plus changed payload reaches B as a changed fingerprint;
- public root/facade exports retain exact owner identity in a clean wheel.

The strict fake is an action-execution seam, not a persistence implementation.
B integration acceptance is its memory/SQLite parity matrix: action receipt
capacity, lease fencing, changed-payload conflict, commit-before-ack replay,
sticky native unknown, request recipient authorization, monotonic generation,
workflow CAS, restart, and no duplicate native invocation.

Focused command:

```sh
PYTHONPATH=src python -m unittest tests.gateway.test_actions -v
```

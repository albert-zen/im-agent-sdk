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

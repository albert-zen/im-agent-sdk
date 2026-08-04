# Gateway routing bindings testing

Gateway integration conformance must continue to prove, through the existing
Gateway operation, persistence, routing, projection, and recovery owners:

- one Conversation has at most one current binding while multiple
  Conversations may bind the same Thread;
- revision compare-and-swap rejects stale writers;
- a same-target crash retry converges only with no guard, the current revision,
  or the immediately preceding revision, and never overwrites a later
  different target;
- an invalid same-target revision fails before route preparation and cannot
  release an existing recovery fence;
- project bind, Thread bind, and Thread clear return the documented typed
  postconditions without activating native UI state;
- mutations for one Conversation serialize while unrelated Conversations can
  progress independently;
- `foreground_only` prepares a route before binding CAS, gives it no authority
  before equality, and removes old-Thread authority after a switch;
- switching one Conversation leaves other Conversations on the old Thread
  active;
- restart rebuilds foreground observation from durable binding state without
  creating another Application subscription; and
- product command grammar, CWD/profile policy, and consumer JSON state are
  absent from the binding implementation.

The focused binding-leaf tests prove typed operation/result shape, facade
identity, field validation, binding-result postconditions, repository/CAS
mutation, same-target convergence, the revisionless retry distinction, and
verification after an unknown write outcome. They use a constructor-injected
repository and assert that a failed or unverifiable write never produces
verified binding authority. They do not claim ownership of Conversation
locks, foreground route authority, worker recovery, or fan-out.

Current integration evidence remains in `tests/test_gateway_operations.py`,
`tests/test_projection_routing.py`, and
`tests/gateway/persistence/test_memory.py`. Focused owner, runtime, and facade
coverage lives in `tests/gateway/routing/test_bindings.py`; it proves that the
routing facade and historical contracts facade expose the exact binding-owner
objects, that the extracted validators preserve existing failures, and that
the sole binding runtime preserves mutation and convergence semantics. The
integration suites continue to exercise unchanged aggregate lock,
route-preparation, recovery-fence, foreground-switch, fan-out, and restart
paths across owners.

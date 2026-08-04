# Gateway routing bindings testing

Binding conformance must prove:

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

Current evidence is in `tests/test_gateway_operations.py`,
`tests/test_projection_routing.py`, and
`tests/gateway/persistence/test_memory.py`. The target mirrored suite is
`tests/gateway/routing/test_bindings.py`; the component map remains explicit
about that pending mechanical move.

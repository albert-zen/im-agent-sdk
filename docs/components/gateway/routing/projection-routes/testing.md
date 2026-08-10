# Gateway projection routes testing

Projection-route conformance must prove:

- stable route identity and uniqueness for each Thread/Conversation edge;
- explicit clear-observation is a closed scoped operation and never changes
  input binding or native UI state;
- required same-Application Project ancestry in every route and rejection of
  Project-less persisted or in-memory Thread references;
- one Thread can fan out to multiple Conversations without creating multiple
  Application observation workers;
- all three policies resolve only their documented active destinations;
- `foreground_only` has no authority before binding equality and stops the old
  Thread after `/new`- or `/pick`-equivalent typed binding changes;
- switching one Conversation does not remove or disable other Conversations'
  routes to the old Thread;
- route refresh preserves checkpoints and replaces destination context under
  generation control;
- checkpoint compare-and-swap cannot be bypassed by ordinary route writes;
- restart reconstructs active observation from durable routes and bindings,
  with bounded baseline-before-live ordering;
- successful and terminally replayed scoped route mutations reconcile the live
  projection owner against current route authority without direct repository
  access from Controller/action surfaces or restoration of later-removed state;
- one destination failure neither restarts Thread observation nor blocks
  another destination;
- request projection creates correlation only for accepted destination
  delivery and does not advance the message checkpoint; and
- proactive destination snapshots do not follow later route changes.

Focused owner evidence lives in
`tests/gateway/routing/test_projection_routes.py`. It proves exact routing
facade identity, clean-process import order and runtime type hints, the absent
historical contracts names, specific validation, stable identity, active-route
policy, and checkpoint-preserving route writes. Integration evidence remains
in `tests/gateway/routing/test_projection_integration.py`, `tests/gateway/projection/test_hardening.py`,
and `tests/gateway/test_operations_integration.py`; it continues to prove the unchanged
binding/CAS sequencing, one-worker fan-out, recovery, checkpoint, request, and
delivery behavior.

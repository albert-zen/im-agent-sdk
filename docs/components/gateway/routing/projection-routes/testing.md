# Gateway projection routes testing

Projection-route conformance must prove:

- stable route identity and uniqueness for each Thread/Conversation edge;
- one Thread can fan out to multiple Conversations without creating multiple
  Application observation workers;
- all three policies resolve only their documented active destinations;
- `foreground_only` has no authority before binding equality and stops the old
  Thread after `/new`- or `/pick`-equivalent typed binding changes;
- switching one Conversation does not remove or disable other Conversations'
  routes to the old Thread;
- route refresh preserves checkpoints and replaces destination context under
  revision control;
- checkpoint compare-and-swap cannot be bypassed by ordinary route writes;
- restart reconstructs active observation from durable routes and bindings,
  with bounded baseline-before-live ordering;
- one destination failure neither restarts Thread observation nor blocks
  another destination;
- request projection creates correlation only for accepted destination
  delivery and does not advance the message checkpoint; and
- proactive destination snapshots do not follow later route changes.

Current evidence is in `tests/test_projection_routing.py`,
`tests/test_projection_hardening.py`, and `tests/test_gateway_operations.py`.
The target mirrored suite is
`tests/gateway/routing/test_projection_routes.py`; the later structural move
must preserve these semantics exactly.

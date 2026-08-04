# Gateway projection routes design

## Purpose and ownership

`gateway.routing.projection-routes` owns durable outbound edges from one
Application Thread to IM Conversations. A `ThreadProjectionRoute` answers
where canonical Thread output may be projected; it does not select input,
activate native UI state, or create an Application subscription.

This leaf owns:

- `foreground_only`, `remembered_last_recipient`, and `all_observers` route
  policy semantics;
- stable route identity and per-Conversation destination references;
- active-route resolution at delivery time;
- route refresh rules that preserve completed projection checkpoints.

It does not own Channel delivery, presentation, delivery idempotency claims,
checkpoint advancement, request response authority, Application history, or
the Thread-scoped observation worker. One Thread may have routes to multiple
current Conversations, but Gateway projection establishes only one
Application observation worker for that Thread and fans canonical observations
out afterward.

## Route policy and identity

Route identity is derived from stable Application/Thread and Conversation
references, never text or timestamps. A Thread/Conversation edge is unique;
refreshing its reply/topic context replaces that edge rather than creating a
second subscription or delivery authority.

- `foreground_only` authorizes a route only while the destination
  Conversation's current binding equals the route Thread.
- `remembered_last_recipient` retains explicitly remembered destinations after
  input selection changes.
- `all_observers` retains all explicitly observed destinations.

For `foreground_only`, Thread binding prepares the additive route and
bootstrap ordering before binding CAS. Equality remains the sole delivery
authority: the route is inactive before the bind and after a later switch.
Changing Conversation A from Thread 1 to Thread 2 therefore stops Thread 1
output to A while leaving every other Conversation observing Thread 1 intact.

## State and recovery

Routes persist only stable endpoint references, optional destination reply
context, the per-destination completed projection checkpoint, and update time.
An ordinary route refresh preserves checkpoint fields. Checkpoint advancement
belongs to the projection checkpoint owner and uses expected-value
compare-and-swap after stable delivery idempotency completion or durable O1
suppression. Opaque Agent item IDs are never ordered or used to infer progress.

On restart, active routes and bindings determine which Thread-scoped workers
must exist. Baseline/history recovery and the per-route bootstrap barrier
belong to projection recovery and observation; they consume route state but do
not create a second route authority or event buffer. A destination delivery
failure is isolated from other routes and from Application observation.

Interactive requests use the same route-selection policy, but request
delivery correlation is stored only after that destination accepts the stable
request delivery. It does not advance an Agent-message checkpoint or make the
route a request-state authority. Proactive delivery pins an immutable resolved
destination snapshot in its own owner rather than following later route
movement.

## Current structural gap

Route values, repository coordination, projection delivery, and request
projection currently share broad modules. Later focused slices will move the
route owner to its target module and leave delivery, checkpoints,
request-correlation, and observation in their respective leaves.

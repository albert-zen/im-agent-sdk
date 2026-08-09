# Gateway projection routes design

## Purpose and ownership

`gateway.routing.projection-routes` owns durable outbound edges from one
Application Thread to IM Conversations. A `ThreadProjectionRoute` answers
where canonical Thread output may be projected; it does not select input,
activate native UI state, or create an Application subscription.

This leaf owns:

- the typed `ObserveThread` and `ThreadObserved` values and their specific
  postcondition validation;
- `foreground_only`, `remembered_last_recipient`, and `all_observers` route
  policy semantics;
- stable route identity and per-Conversation destination references;
- active-route resolution at delivery time;
- route refresh rules and route persistence shape that preserve completed
  projection checkpoints without advancing them.

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
The route's required `ThreadRef.project_ref` supplies its Project ancestry;
route identity and storage never use an empty-string Project sentinel or a
Project-less fixed/flat exception.
An ordinary route refresh preserves checkpoint fields. Checkpoint advancement
belongs solely to the projection checkpoint owner. It uses expected-value
compare-and-swap after stable delivery idempotency completion or durable O1
suppression; this route leaf never invokes that CAS. Opaque Agent item IDs are
never ordered or used to infer progress.

On restart, active routes and bindings determine which Thread-scoped workers
must exist. Projection recovery owns bounded baseline/history reads and gap
policy; observation owns the sole per-route bootstrap barrier and exposes only
narrow current-route and ordered-delivery collaboration to recovery, without
creating a second barrier, route authority, or event buffer. A typed
destination decision failure is isolated from other routes and from Application
observation. Correlation-repository reads and checkpoint CAS are deliberately
outside that catch so transient infrastructure failure enters the affected
Thread's existing supervisor rather than permanently blocking a route.

Interactive requests use the same route-selection policy, but request
delivery correlation is stored only after that destination accepts the stable
request delivery. It does not advance an Agent-message checkpoint or make the
route a request-state authority. Proactive delivery pins an immutable resolved
destination snapshot in its own owner rather than following later route
movement.

## Physical boundary

The canonical implementation is
`src/imagent/gateway/routing/projection_routes.py`. It owns the exact
`ObserveThread`, `ThreadObserved`, and `ProjectionPolicy` objects, the
operation/result validators, stable route-ID derivation, active-route
resolution, and checkpoint-preserving repository refresh/replacement. The
route values resolve their `ThreadProjectionRoute` annotation directly from
the passive-state owner; no temporal annotation replacement or duplicate
operation union is used.

The Gateway aggregate keeps Conversation serialization and Application Thread
truth validation, then delegates the route mutation through its existing typed
private method. The route authority returns route-refresh facts to that
orchestration site, so remembered-route cleanup remains explicitly with the
request/delivery coordinator owners. Observation workers, bootstrap barriers,
the checkpoint authority, bounded recovery, request correlation, Channel
delivery, and proactive snapshotting continue to consume the same route state
and do not gain a second repository, subscription, runtime, or authority.

`imagent.gateway.routing` is the finite public facade for the moved route
operation values. The historical `imagent.contracts` facade and its internal
operation/validator modules deliberately have no `ObserveThread` or
`ThreadObserved` attribute or `__all__` entry.

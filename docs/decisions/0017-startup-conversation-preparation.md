# ADR 0017: Conversation preparation before Gateway recovery

Status: Accepted

## Evidence and decision

The IMZen PAW consumer can persist a Conversation as delegated to a different
product runtime while an older SDK foreground Thread binding survives. The v1
public actions become available only after Gateway startup has already released
projection and pending-request recovery. Clearing that binding after `start()`
can therefore send old Thread output or approvals to the wrong consumer mode.
An inbound-only gate cannot prevent this. Suppressing output in a presenter does
not cover requests and can falsely consume delivery evidence. This is a concrete
counterexample under ADR 0006, not a reason to restore repository access.

`GatewayExtensions.startup_preparations` is an immutable tuple of explicitly
configured `StartupPreparation` values. Each names one Conversation, one actor,
and one async callback. After the coherent lease and workspace check, but before
any projection recovery, adapter startup, or Channel admission, Gateway runs
these callbacks in tuple order. Each receives only `StartupConversationActions`:
its fixed Conversation/actor, `get_binding()`, and the existing generation-guarded
`clear_thread(action_id=..., expected_generation=...)` action. It cannot discover
other Conversations, create native resources, dispatch input, or send output.
Consumer policy chooses the scopes, condition, stable action ID, and treatment of
a non-success result. The SDK owns bounded ordering, action/store fencing, and
lifecycle failure. No SDK PAW state or product mode is added.

The narrow surface delegates to the same scoped action engine and durable
receipts as runtime actions. It is valid only in its callback's task and only
until that invocation ends. A spawned task or retained surface cannot mutate
later runtime state. Public `Gateway.actions()` remains unavailable until the
normal startup transition completes.

The tuple has an explicit positive finite `startup_preparation_max_items` limit;
overflow fails before store or adapter I/O, never truncates. Each invocation uses
the existing hard `lifecycle_owner_timeout_seconds` bound. Failure, cancellation,
timeout, or invalid callback output prevents startup and closes the coherent
session/store. No inbound processing, Application/Channel startup, recovered
projection, or pending-request delivery occurs first. A partial successful clear
is durable, not rolled back or compensated. Retrying uses a fresh Gateway and
stable guarded actions, rather than replaying hidden consumer work.

Absence leaves the existing startup ordering unchanged. This is one pre-recovery
bridge-state preparation position, not a generic middleware or lifecycle stage
registry. It does not revoke previously delivered request authority; existing
request-correlation policy and Thread route selection remain authoritative.

## Verification

Public SQLite reconstruction must show a persisted foreground binding cleared
before any old output or request can recover, while unaffected Conversations
still recover. Failure/timeout/cancellation, capacity, stale generation,
retained/spawned-task use, concurrent start/stop, lease loss, and exact cleanup
must be covered without exposing private repository/runtime owners to consumers.

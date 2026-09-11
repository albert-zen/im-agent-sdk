# DeepSeek Harness Application adapter design

Component ID: `applications.adapters.deepseek-harness`

Parent: `applications.adapters`

## Purpose and ownership

This leaf owns the DeepSeek Harness concrete Application adapter over the DeepSeek
Harness Web Host RPC surface: native workspace/Project and session/Thread
resources, input dispatch, history/catch-up, interruption, and live event
recovery through bounded native-history polling. DeepSeek Harness sessions,
workspaces, events, and logs remain authoritative.

It does not own DeepSeek Harness product policy, the Web Host network trust
boundary, bundled runtime resolution, attachment handling, or interactive
request response. The adapter never creates a second transcript.

## Typed boundary and public facade

Inputs are a typed DeepSeek Harness Web Host client, adapter configuration, and
common Application operations/inputs. Outputs are `ApplicationSummary`, typed
operation results, `AcceptedTurn` or explicit `ApplicationInputOutcomeUnknown`,
and canonical `AgentEvent` values.

The current formal exports are `DeepSeekHarnessApplicationAdapter` and
`HttpDeepSeekHarnessClient` from the lazy `imagent.applications` facade,
implemented at the exact target
`imagent.applications.adapters.deepseek_harness`. The top facade preserves
object identity and lazy cold-import behavior.

## Dependencies, state, and recovery

The adapter depends on Interaction messages/operations, Applications
contract/capabilities/events/operations, and `httpx` for the Python Web Host
client. Projects are managed: `workspace.list` is native discovery and reading;
project creation/deletion remain unsupported until native consumer evidence
requires them.

Sessions are Thread identities and native session IDs are used verbatim.
`session.list` plus `workspace.list` session membership drives Thread listing and
scoped reads. `session.history` is the recovery authority for history and
catch-up and is also the live event source: a per-Thread poll task reads fresh
history pages and publishes only sequence numbers not yet observed by that
process. Seen-sequence baselines and poll tasks are process-local, bounded,
and rebuildable; stop/reset clears them.

`send_input` calls `session.prompt` with a single text block, then waits on
native history polling until DeepSeek Harness records the new `turn/start`.
DeepSeek Harness `session.prompt` itself returns only an enqueue receipt, so the
adapter never fabricates a Turn identity and emits `ApplicationInputOutcomeUnknown`
when no native `turn/start` is admitted before the configured timeout.

Turn identity is the native integer `turn` value serialized as a string.
History groups raw events by that value into SDK turns; `turn/end` reason kinds
map to typed terminal statuses. Event fan-out publishes `MESSAGE_COMPLETED` for
committed `assistant/message` events and the typed `TURN_*` terminal event for
`turn/end`.

## Capabilities

- Projects: managed, native discovery and reading, no creation/deletion.
- Threads: native list/create/read, no deletion.
- Runtime: native history, streaming, and interruption; no replay, requests,
  or explicit native-thread activation.

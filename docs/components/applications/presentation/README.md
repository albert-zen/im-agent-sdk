# Applications presentation navigation

These leaves are the ADR 0015 A1 adapter-owned positions. They run only in a
concrete adapter's existing ordered native event/history normalization path.
They are not a common `AgentApplicationAdapter` method, a generic hook, raw
native event callback, service locator, or Gateway extension.

| Leaf | Responsibility | Design | Testing |
|---|---|---|---|
| `applications.presentation.live-activity` | bounded non-artifact live/recoverable text presentation | [design](live-activity/design.md) | [testing](live-activity/testing.md) |
| `applications.presentation.artifact-materialization` | bounded untrusted App Server artifact candidates and typed attachment result | [design](artifact-materialization/design.md) | [testing](artifact-materialization/testing.md) |

Each concrete adapter fixes native item/Turn/event identity, role,
recoverability, and checkpoint behavior before consumer code runs. Consumers
never receive raw payloads, Application clients, credentials, bindings,
routes, checkpoints, byte values, or trusted filesystem authority.

# Accepted decisions

This document records decisions that have completed design review. Reopening a
decision requires updating the relevant authoritative documents in the same
change.

## D-001: Language-neutral contracts with a Python reference

The semantic contract is published as JSON Schema. The first reference
implementation and test kit use Python because the existing IMCodex-family
channel edge is Python.

The Python package is not the wire protocol. TypeScript and other
implementations may consume the same schemas.

## D-002: Project is first-class but not required at runtime

Project remains part of the common resource hierarchy:

```text
AgentApplication
  └── Project
        └── Thread
```

An application instance declares one project mode:

```text
managed — projects are discoverable/selectable resources
flat    — threads live directly under the application instance
fixed   — the application instance has one externally configured cwd/workspace
```

`flat` and `fixed` are supported operating modes, not missing implementations.
They expose threads without `projectRef`. An adapter must not synthesize a
misleading Project merely to satisfy the hierarchy.

## D-003: Conversation-level selection

The initial binding key is:

```text
(channelInstanceId, conversationId)
```

Per-user selection inside a group conversation is a future product extension,
not part of the first common binding contract.

## D-004: Archive and permanent deletion are distinct

Thread deletion capability is:

```text
unsupported | archive | permanent
```

An adapter must report the actual supported behavior. A permanent-delete
request cannot be silently implemented as archive.

## D-005: Design authority before implementation

The Vision, Architecture, Domain Model, Protocol, Adapter, and Decision
documents define the shared boundary. Runtime code cannot introduce new common
semantics without first documenting them.

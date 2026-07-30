# ADR 0001: Contract and resource foundations

Status: Accepted

## Context

IM and Agent products use different languages, Project models, identity
scopes, and deletion behavior. A Python-only or universally managed-Project
model would misrepresent real integrations.

## Decision

### Language-neutral contracts

Semantic contracts are published as JSON Schema. Python is the first reference
implementation and test kit, not the wire protocol.

### Project is first-class but optional at runtime

Applications declare:

- `managed`: authoritative Projects contain Threads;
- `flat`: Threads live directly under the Application;
- `fixed`: one externally configured workspace/cwd contains flat Threads.

Flat/fixed adapters omit `ProjectRef` instead of synthesizing a misleading
Project.

### Conversation-level selection

The initial input binding key is
`(channelInstanceId, conversationId)`. Per-user group selection remains future
consumer policy.

### Deletion semantics

Thread deletion capability is `unsupported`, `archive`, or `permanent`.
Permanent deletion cannot be silently implemented as archive.

## Consequences

References remain scoped by configured Application instances. Adapters expose
their real Project and deletion shape. Cross-language implementations validate
against the same schema.

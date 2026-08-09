# ADR 0001: Contract and resource foundations

Status: Accepted; Project-less flat/fixed representation superseded by ADR 0016

## Context

IM and Agent products use different languages, Project models, identity
scopes, and deletion behavior. A Python-only or universally managed-Project
model would misrepresent real integrations.

## Decision

### Language-neutral contracts

Semantic contracts are published as JSON Schema. Python is the first reference
implementation and test kit, not the wire protocol.

### Project is first-class and uniform at runtime

Applications declare:

- `managed`: authoritative Projects contain Threads;
- `flat`: one stable adapter workspace Project contains Threads;
- `fixed`: one stable configured workspace Project contains Threads.

ADR 0016 supersedes the original Project-less flat/fixed representation.
Flat/fixed adapters expose an honest stable workspace scope while continuing
to reject unsupported native Project management.

### Conversation-level selection

The initial input binding key is
`(channelInstanceId, conversationId)`. Per-user group selection remains future
consumer policy.

### Deletion semantics

Thread deletion capability is `unsupported`, `archive`, or `permanent`.
Permanent deletion cannot be silently implemented as archive.

## Consequences

References remain scoped by configured Application instances. Every Thread has
one Project/Workspace ancestor, while adapters expose their real management and
deletion capabilities. Cross-language implementations validate against the
same schema.

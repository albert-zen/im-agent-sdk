# ADR 0002: Design authority and control boundaries

Status: Accepted

## Context

Fast implementation can create common semantics accidentally. Earlier command
flows also mixed product Slash UX, Application mutations, and Gateway binding
state.

## Decision

### Design authority before implementation

Vision, Architecture, accepted ADRs, Contracts, and affected component docs
define the shared boundary. Runtime code cannot introduce new common semantics
before those durable sources are reviewed.

### Typed Application and Gateway Operations

Application Operations have behavior-specific fields/results and mutate or
read one native Application. Gateway Operations mutate Gateway-owned
Conversation selection or output observation. Free-form Metadata is not used
for common arguments or success values.

Binding a Thread validates the native resource but does not activate/open it
in the native UI.

### Default Slash UX is optional

Gateway may compose an inbound Controller but does not parse Slash syntax or
own presentation. The SDK may ship a replaceable Slash Controller and Markdown
presenter over typed actions. Buttons and other interactions invoke those same
actions without manufacturing text commands.

Inbound and outbound envelopes are distinct; an outbound `deliveryId` is a
request identity, not a native message observation.

## Consequences

Product commands and presentation can evolve without expanding Gateway/Core.
Native callers and Controllers share typed semantics. Cross-component changes
must update durable design first.

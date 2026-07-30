# Schema v1

These JSON Schemas describe semantic contracts, not a required transport.

- `common.schema.json`: IDs, timestamps, metadata, and errors.
- `resources.schema.json`: applications, projects, threads, and status.
- `capabilities.schema.json`: Channel and Agent application capabilities.
- `messages.schema.json`: conversations, content parts, and messages.
- `history.schema.json`: Turn catch-up and Thread history results.
- `operations.schema.json`: operations and results.
- `events.schema.json`: unified Agent events.
- `bindings.schema.json`: current IM Conversation selection.

Python reference objects add cross-reference invariants that JSON Schema cannot
express conveniently, such as ensuring a selected Project and Thread belong to
the same application instance.

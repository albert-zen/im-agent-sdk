# Schema conformance design

## Purpose

Schema conformance keeps the versioned JSON Schema surface discoverable and
self-consistent. It validates schema documents and their references without
claiming ownership of the semantic contract represented by any one schema.

## Authority and ownership

JSON Schema is the language-neutral contract surface; Python is the first
reference implementation and test kit, not the wire protocol. Interaction,
Gateway, and Applications runtime leaves own the meaning of their fields and
the cross-owner unions that are intentionally published in one versioned
document. This engineering leaf owns only:

- schema inventory and navigation;
- document IDs, draft declarations, and local reference resolution;
- deterministic structural validation of the versioned schema set; and
- routing contributors to the runtime leaf that owns a semantic change.

It does not own runtime behavior, generated native models, compatibility
policy, transport encoding, or a second contract vocabulary.

## Executable boundary

`scripts/validate_schemas.py` is the single implementation of the structural
check. The focused executable evidence for that implementation lives at
`tests/engineering/test_schema_conformance.py`. The test module invokes the
real validator entry point against the checked-in inventory and small,
deterministic temporary inventories. It supplies fixture documents only to
exercise the validator's existing branches; it does not reimplement inventory,
ID, reference, or JSON Pointer validation.

The engineering test module is a repository-maintainability aid, not a second
schema authority. It must remain independent of runtime adapters and must not
introduce generated models, a schema registry, or a second validation path.

## Version and reference rules

The current schema set is under `schemas/v1/`. Each schema has a unique `$id`
and declares JSON Schema 2020-12. A `$ref` points to a schema in the same
versioned set or to a JSON Pointer within the current document; an unresolved
target or pointer fails validation. The validator checks the complete set in a
deterministic order and uses `jsonschema` for draft validation when that
optional development dependency is installed.

Schemas may remain cross-owner when one document intentionally publishes a
stable union—for example, common values shared by runtime layers. That does
not grant the engineering validator ownership of those values. A semantic
change must update the owning component design, testing page, applicable ADR,
schema, and Python contract evidence together.

## Change boundary

When a schema changes, identify the owning runtime leaf from the component map,
then check all consumers of the affected `$ref` and all public Python
validators. Do not add a schema field to encode product commands, native
credentials, transcript state, execution state, or an unbounded payload. Keep
stable identities explicit and preserve the distinction between Message
content and Operation control intent.

The v1 resource schemas encode the same unconditional hierarchy as Python:
`ProjectRef(applicationInstanceId, projectId)`,
`ThreadRef(projectRef, threadId)`, and `TurnRef(threadRef, turnId)`. Schemas for
events, history, requests, bindings, correlations, operations, and proactive
targets reuse those definitions; they do not publish nullable or duplicate
Project ancestry.

Schema conformance is a repository check. It must remain stateless and must
not introduce generated code, a schema registry, a persistence layer, or a
runtime admission path.

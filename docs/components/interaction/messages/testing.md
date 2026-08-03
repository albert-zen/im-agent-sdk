# Interaction messages testing

## Contract scenarios

Tests for `interaction.messages` must prove:

- inbound, outbound, and Agent envelopes use their distinct stable identity
  fields and reject missing or oversized identifiers;
- content is non-empty, typed, ordered, and round-trips through the v1 schema;
- plain and Markdown text discriminants remain aligned between Python and JSON
  Schema;
- native reply identity and timestamps remain descriptive and never become
  deduplication keys;
- a canonical Agent item preserves `agentItemId`, Thread scope, role,
  `clientMessageId`, ordered content, and bounded normalized Metadata through
  live and authoritative-history projection;
- Metadata cannot retarget delivery, alter checkpoint identity, or carry an
  attachment location in place of typed media content;
- immutable values do not expose a mutable shared Gateway/Application context.

Current focused evidence is:

```sh
PYTHONPATH=src python -m unittest tests.test_contracts -v
python scripts/validate_schemas.py
```

During the mechanical move, equivalent focused coverage moves once to
`tests/interaction/test_messages.py`; the historical test remains only until
all of its other owner-specific cases have moved.

Any schema or public-contract change also requires Python/schema discriminant
parity, concrete adapter conformance where the envelope crosses a native
boundary, component-map validation, and the repository-wide checks in
`AGENTS.md`.

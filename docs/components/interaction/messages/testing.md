# Interaction messages testing

## Contract scenarios

Tests for `interaction.messages` must prove:

- inbound and outbound envelopes use their distinct stable identity fields and
  reject missing or oversized identifiers;
- content is non-empty, typed, ordered, and round-trips through the v1 schema;
- plain and Markdown text discriminants remain aligned between Python and JSON
  Schema;
- native reply identity and timestamps remain descriptive and never become
  deduplication keys;
- Applications-owned `AgentMessage` composes these content and Metadata values
  without adding an Interaction dependency;
- Metadata cannot retarget delivery, alter checkpoint identity, or carry an
  attachment location in place of typed media content;
- immutable values do not expose a mutable shared Gateway/Application context.

The dependency-safe message-foundation extraction establishes focused
evidence at:

```sh
PYTHONPATH=src python -m unittest \
  tests.interaction.test_messages \
  tests.test_contracts -v
python scripts/validate_schemas.py
```

`tests/interaction/test_messages.py` owns exact facade identity, closed ordered
content, text discriminants, and distinct inbound/outbound identity. The
Applications contract owns canonical Agent item, Application history/event,
and `ThreadRef` cases; Gateway correlation and schema cases remain with their
owners. Those cases are not duplicated in the Interaction foundation test.

Any schema or public-contract change also requires Python/schema discriminant
parity, concrete adapter conformance where the envelope crosses a native
boundary, component-map validation, and the repository-wide checks in
`AGENTS.md`.

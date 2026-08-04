# Schema conformance testing

## Focused validation

Run the schema validator from the repository root:

```sh
uv run python scripts/validate_schemas.py
```

Run the focused executable tests for that validator as well:

```sh
PYTHONPATH=src:tests uv run python -m unittest tests.engineering.test_schema_conformance -v
```

`tests/engineering/test_schema_conformance.py` calls the real
`scripts/validate_schemas.py` entry point. Its temporary fixtures are only
minimal schema documents used to exercise existing validator branches; the
tests do not copy the validator's inventory or reference-walking logic.

The focused contract tests exercise the Python reference invariants that JSON
Schema cannot conveniently express:

```sh
PYTHONPATH=src uv run python -m unittest tests.interaction.test_contracts -v
```

The documentation and ownership checks should also be run for a schema change:

```sh
uv run python scripts/check_doc_links.py
uv run python scripts/validate_component_map.py
```

## Required evidence

The validator must prove, for every `schemas/v1/*.schema.json` document:

- the file set is non-empty and parsed as JSON;
- `$id` exists and is unique;
- `$schema` is JSON Schema 2020-12;
- every local or same-set `$ref` resolves to an existing document; and
- every referenced JSON Pointer resolves to an existing value.

The focused tests must also retain executable evidence for the empty-inventory,
malformed-JSON, missing or duplicate schema-ID, wrong-draft, unresolved-file
reference, and missing-pointer failure paths. These cases are expected to
fail closed: the validator must raise or exit before reporting a successful
validation result.

When the development dependency is available, `jsonschema` must also accept
each document as a valid schema. A validator failure is a release-blocking
repository error; it is not repaired by ignoring a reference or weakening the
runtime contract test.

## Semantic change checklist

For a schema semantic change, add evidence at the owning runtime leaf:

1. update its design and testing pages before implementation;
2. update the relevant Python contract and focused tests;
3. verify all cross-owner schema references and public exports;
4. run the conformance kit when the changed value is shared; and
5. record an accepted decision when the change crosses a component boundary.

Do not treat schema validation as proof of adapter behavior. It proves the
language-neutral document is structurally sound; contract and native adapter
tests prove the implementation tells the truth.

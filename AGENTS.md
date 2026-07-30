# AGENTS.md

IM Agent SDK connects IM channels to Agent applications without becoming an
Agent runtime itself.

## Invariants

1. Agent applications own project, thread, turn, transcript, and execution
   truth. The SDK may cache projections but must be able to rebuild them.
2. The SDK owns only channel delivery state, conversation bindings,
   idempotency records, and reconnect cursors.
3. `Message` carries content. `Operation` carries control intent. Do not encode
   thread management as magic chat text inside the core.
4. Every externally visible mutation has a stable ID. Never deduplicate by
   comparing message text or timestamps.
5. `ProjectRef` and `ThreadRef` are scoped by an Agent application instance.
   Native identifiers are opaque to the SDK.
6. Channel-specific Markdown, chunking, attachment, and delivery behavior stays
   in Channel adapters.
7. Application-specific workspace, model, provider, sandbox, and runtime-mode
   behavior stays in Agent application adapters.
8. Streaming deltas are transient events. Completed messages and authoritative
   history come from the Agent application.
9. Unsupported behavior must be reported through capabilities or explicit
   errors. Do not silently approximate destructive operations.
10. Production adapters should migrate proven implementations with recorded
    provenance before inventing replacements. Preserve license notices and
    source commit IDs.

11. During the design-first phase, do not add runtime code before the
    architecture and contract changes are accepted in the authoritative docs.

## Authoritative documents

- `docs/VISION.md`: purpose and product direction.
- `docs/ARCHITECTURE.md`: boundaries, state ownership, and data flow.
- `docs/DOMAIN_MODEL.md`: application, project, thread, and binding resources.
- `docs/PROTOCOL.md`: message, operation, result, and event contracts.
- `docs/ADAPTERS.md`: Channel and Agent application adapter responsibilities.
- `docs/DECISIONS.md`: accepted architectural decisions.
- `docs/REUSE.md`: source reuse, provenance, and extraction policy.
- `docs/ROADMAP.md`: milestones and unresolved decisions.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
python scripts/validate_schemas.py
```

# Dependency rules

The machine-readable
[component map](../components/component-map.yml) is the single ownership and
dependency authority. AgentKit does not maintain a second hand-written layer
graph.

The target runtime direction is:

```text
Interaction  ←  Applications
      ↑          ↑
      └─ Gateway ┘
```

- Interaction is the lowest IM/message/operation/Controller/Channel surface.
- Applications may depend on Interaction and never on Gateway.
- Gateway composes Interaction and Applications; neither lower layer imports
  Gateway implementation.
- Engineering support may consume all three runtime layers; runtime code does
  not depend on engineering helpers.
- The exact typed Controller exceptions are declared individually in the map.
  They do not permit a general Interaction-to-Gateway/Application dependency.

## Import resolution during the physical migration

Current broad modules such as `contracts`, `adapters`, and `diagnostics` are
explicit split candidates. Architecture lint therefore resolves every
`src/imagent` internal import through component ownership rather than treating
the current filename as a component:

1. For `from module import Symbol`, an exact mapped current public export owns
   `Symbol`; otherwise lint falls back to the imported module's declared owner
   set.
2. For `import module`, the imported module's declared owner set applies.
3. The import is allowed only when at least one source owner is the target
   owner or declares that target component as a direct dependency.
4. Multiple owners do not grant a blanket exemption. They provide candidate
   pairings, and at least one explicit pairing must authorize the import.
5. A formal facade may re-export only a symbol whose facade reference is
   mapped to an owner that also owns the imported implementation path.
6. Unknown internal modules, unmapped source/target paths, dependency cycles,
   unused exact exceptions, and imports with no authorized owner pairing fail.

This rule is intentionally transitional but not permissive: moving a symbol
changes its current/target path binding in the same slice, while its component
dependency remains authoritative. No broad legacy-import allowlist is kept.

Run `python scripts/agentkit.py lint-architecture` after changing ownership,
exports, internal imports, or physical module layout. `agentkit check` runs the
same component-map lint.

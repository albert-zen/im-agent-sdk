# Engineering support

Engineering support is the repository-facing part of IM Agent SDK. It keeps
contracts honest, validation reproducible, ownership navigable, and releases
installable. It is outside the runtime dependency graph: Interaction owns IM
messages, media, Controllers, and Channels; Gateway owns bridge composition and
state; Applications owns native Application contracts and adapters.

The engineering leaves are the authority for their named responsibility:

| Leaf | Authority |
|---|---|
| [Testing and conformance](testing-and-conformance/design.md) | Reusable contract checks, bounded fakes, and evidence that integrations tell the truth. |
| [Schema conformance](schema-conformance/design.md) | Language-neutral schema discovery, reference validation, and schema navigation. |
| [Repository maintainability](repository-maintainability/design.md) | Ownership maps, documentation, repository checks, provenance, and maintainability budgets. |
| [AgentKit](agentkit/design.md) | Pinned lifecycle tooling, change routing, architecture gates, and review guidance. |
| [Release](release/design.md) | Package metadata, public facades, typing markers, wheels, and clean-install verification. |
| [Reference consumer](reference-consumer/design.md) | The one public-path executable acceptance consumer, onboarding, and focused vertical evidence. |

## Reading order

Read the global authorities before choosing an engineering leaf:

1. [Vision](../VISION.md) defines purpose, non-goals, and the evidence
   threshold for shared semantics.
2. [Architecture](../ARCHITECTURE.md) defines the three runtime layers and the
   boundary of engineering support.
3. [Accepted decisions](../decisions/README.md) define reviewed
   cross-component constraints.
4. The [component map](../components/component-map.yml) binds code, tests,
   docs, exports, dependencies, and structural gaps.

Each leaf has a `design.md` and a `testing.md`. The design page is the
authority for ownership and invariants; the testing page is the authority for
repeatable evidence. A leaf may point to runtime component docs for semantic
details, but it must not duplicate their ownership authority.

## Transitional navigation

The former broad pages remain at their old paths so existing links continue to
resolve. They are navigation only and contain no separate design authority:

- [transitional testing-and-conformance page](../components/testing-and-conformance/design.md);
- [transitional repository-maintainability page](../components/repository-maintainability/design.md).

The component map and AgentKit routing point to the focused leaves above.

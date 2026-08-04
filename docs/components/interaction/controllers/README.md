# Interaction Controller components

Controllers are optional, product-neutral Interaction UX. They recognize a
bounded interaction such as a Slash command and invoke typed actions; they do
not become a message middleware layer, Gateway service locator, or native
Application authority.

## Leaves

| Leaf | Purpose | Documents |
|---|---|---|
| controller-contract | Optional inbound Controller and its minimum typed action surface | [design](controller-contract/design.md), [testing](controller-contract/testing.md) |
| command-registry | Explicit local command composition, validation, freezing, bounds, and handler outcome rules | [design](command-registry/design.md), [testing](command-registry/testing.md) |
| common-commands | SDK common command definitions and bounded portable presentation | [design](common-commands/design.md), [testing](common-commands/testing.md) |
| request-presentation | Typed approval/input rendering without request or authorization ownership | [design](request-presentation/design.md), [testing](request-presentation/testing.md) |

## Dependency boundary

Controller leaves may depend on Interaction messages/operations and the exact
typed Application/Gateway contracts listed in the component map. A configured
Controller receives only `ControllerActions`; it never receives Gateway,
repositories, adapters, a mutable context bag, or another extension seam.
Product handlers receive their own strongly typed services through constructor
injection in the consumer.

The registry is an explicit composition-local object. There is no global or
import-time registration. SDK common commands and consumer product commands
use the same registry instance, which freezes before runtime input is accepted.

## Rollout state

The Controller contract, registry, request presentation, common commands, and
portable Markdown implementation now live under
`src/imagent/interaction/controllers`. `imagent.interaction.controllers` is
the sole formal public facade; the historical `imagent.controllers` package
has been removed and must be absent and unimportable in every import order.
The historical [Controllers overview](../../controllers/design.md) remains
navigation evidence, not a substitute for these leaf contracts. The
machine-readable [component map](../../component-map.yml) records each
current path, target path, test owner, export, dependency, and remaining
structural gap.

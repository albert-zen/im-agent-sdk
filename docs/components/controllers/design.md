# Controllers navigation

The authoritative Controller design is split across the approved Interaction
leaves:

- [Controller contract](../interaction/controllers/controller-contract/design.md);
- [Command registry](../interaction/controllers/command-registry/design.md);
- [Common commands](../interaction/controllers/common-commands/design.md);
- [Request presentation](../interaction/controllers/request-presentation/design.md).

See the [Controller subtree navigation](../interaction/controllers/README.md)
and machine-readable [component map](../component-map.yml) for current/target
code, exports, dependencies, tests, decisions, and structural gaps.

This historical broad path remains temporarily for repository navigation and
AgentKit compatibility during the physical rollout. It is not a fifth
Controller component, does not override the leaf contracts, and does not
restore the removed `imagent.controllers` import path.

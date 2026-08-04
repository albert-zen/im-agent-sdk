# Controllers testing navigation

The authoritative Controller test obligations are owned by the focused leaves:

- [Controller contract testing](../interaction/controllers/controller-contract/testing.md);
- [Command registry testing](../interaction/controllers/command-registry/testing.md);
- [Common commands testing](../interaction/controllers/common-commands/testing.md);
- [Request presentation testing](../interaction/controllers/request-presentation/testing.md).

The optional Controller/common-command parity implementation is exercised by
`tests/interaction/controllers/test_optional_controller.py`, alongside the
typed Gateway/Application suites named by those leaf documents. The machine-readable
[component map](../component-map.yml) records the finite migration to mirrored
`tests/interaction/controllers/` paths. Public-path cleanup also requires the
formal Interaction facade identity/type-hint checks and clean-process evidence
that `imagent.controllers` is absent and unimportable.

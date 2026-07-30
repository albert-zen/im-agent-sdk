# Dependency rules

The configured Python layers encode dependency direction already present in
the code. They do not define product ownership; component docs do that.

```text
contracts
├── ports
├── media
├── state
└── controllers

eventing + contracts + ports + state
└── recovery

contracts + ports + media + eventing
├── application_adapters
└── channel_adapters

contracts + ports + state + controllers + recovery
└── gateway

contracts + ports + eventing
└── testing
```

Rules:

- Language-neutral Contracts import no runtime port or implementation.
- Python runtime Ports depend only on Contracts.
- Media trust helpers depend only on Contracts.
- Event fan-out primitives are independent of integrations.
- Persistence implementations may depend on Contracts and Ports, never on
  concrete integrations.
- Controllers translate UX into typed contracts and do not depend on Gateway
  implementation.
- Concrete adapters do not import Gateway.
- Gateway may compose lower layers, but lower layers never call back into it.
- Application integrations may depend on Contracts, Media, and Eventing.
- Channel integrations may depend on Contracts, Ports, and Media.
- The reusable test kit targets Contracts, Ports, and Eventing.

Run `python scripts/agentkit.py lint-architecture` after moving modules or
changing internal imports.

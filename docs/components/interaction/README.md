# Interaction components

Interaction is the product-neutral IM boundary. It owns content-bearing
messages, the common control-intent vocabulary, explicit media trust values,
bounded Controller composition, and Channel ingress/delivery contracts. It
does not own Gateway orchestration or native Agent truth.

## Value leaves

- [Messages](messages/design.md) — inbound, outbound, and canonical Agent
  content envelopes; see [testing](messages/testing.md).
- [Operations](operations/design.md) — the common typed operation result,
  error, and validation vocabulary; see [testing](operations/testing.md).
- [Media](media/design.md) — attachment sources and source/type/size/trust
  boundaries; see [testing](media/testing.md).
- [Controllers](controllers/README.md) — optional typed Controller contract,
  explicit bounded command composition, common commands, and request
  presentation.

`Message` carries content. `Operation` carries control intent. A Controller
may recognize a command in an inbound Message and invoke a typed action, but
the command text does not become message content at an Application boundary
and product commands do not enter SDK Core.

## Remaining subtree

The approved target also contains the following Channel leaves. Their current
broad documentation remains authoritative until their focused rollout slices
create leaf documents:

```text
channels
├── channel-contract
├── ingress
├── outbound-delivery
└── adapters
```

See the exhaustive [component map](../component-map.yml) for current and target
code paths, tests, exports, dependencies, accepted decisions, and declared
split candidates. The map, rather than current filenames, defines ownership
during the mechanical migration.

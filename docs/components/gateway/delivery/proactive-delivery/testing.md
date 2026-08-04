# Gateway proactive delivery testing

The mirrored proactive suite covers authorization/scope, first-reservation
route pinning, origin/principal namespace isolation, concurrent duplicate
suppression, payload/target conflicts, complete preflight, sticky unknown,
retryable-only resumption and retry-after, partial multi-destination results,
redaction, SQLite restart, and O2 integration.

Route pinning includes both sides of the reservation race: a retry that finds
an existing record must not re-resolve a moved route, while concurrent initial
callers that resolved different snapshot sets must receive an explicit
conflict rather than treating the winner's destination as their own replay.

The in-memory submission repository's atomic reservation/conflict/CAS cases
live in `tests/gateway/persistence/test_memory.py`; this suite consumes that
owner without reintroducing persistence inside delivery orchestration.

Ownership tests additionally prove exact Gateway/proactive facade identity,
that the eight vocabulary definitions and validator are single-owned by
`gateway.delivery.proactive`, that `gateway.persistence.state_contracts` is the
sole passive definition, and that `contracts.delivery` has no definition or
compatibility alias for them, that the eight historical
`imagent.contracts` proactive names are absent in a clean process, and that
the owner seam's finite `__all__` contains exactly those eight names. Both
historical implementation modules remain absent. The tests also prove that
`DeliverySubmissionOrigin` stays available through the Gateway persistence and
delivery facades, while passive state/helper imports remain one-way.
The mirrored Gateway JSON/CLI ingress suite continues to cover authorization-before-staging,
cancellation join, cleanup, route/result mapping, and loopback CLI policy.
Pure decoded-byte, path-confinement, digest, and staged-content construction
cases live in `tests/interaction/test_media_staging.py`.

The runtime predecessor also proves that `proactive_runtime.py` is the sole
implementation owner of `ResolveThreadRoutes`, `DeliveryRouteError`,
`ProactiveDeliveryService`, and its private orchestration helpers; the old
`proactive.py` runtime definitions and attributes are absent. The formal
`imagent.gateway.delivery` and `imagent.gateway` facades import those runtime
objects directly and preserve exact object identity. Clean-process imports,
cycle-safe import orders, Protocol signatures, resolved `get_type_hints`, AST
owner-set checks, and the existing behavior suite guard the mechanical nature
of the split. The one-time migration review compared the moved definitions to
their predecessor; durable tests do not depend on Git history being available
in a shallow source checkout. The vocabulary and validator definitions remain
in `gateway.delivery.proactive`; the clean-import test covers the public graph
and rejects a reverse `contracts.delivery` compatibility import. The
submission origin/state, records/reservations, metadata/conversation helpers,
and identity helpers remain in their accepted owners.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_runtime tests.gateway.delivery.test_proactive_delivery tests.gateway.delivery.test_proactive_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

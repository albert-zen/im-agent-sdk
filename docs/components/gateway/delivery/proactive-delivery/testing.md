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
that the temporary vocabulary definitions remain single-owned by
`contracts.delivery`, that the eight historical `imagent.contracts` proactive
names are absent in a clean process, and that both historical implementation
modules remain absent. They also prove that `DeliverySubmissionOrigin` stays
available through the deliberate `imagent.contracts` passive-state facade.
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
in `contracts.delivery` temporarily for #216; this slice only changes their
public facade and internal import path. The clean-import test covers the
current public graph and does not authorize a reverse `contracts.delivery`
compatibility import. #216 owns the later definition move.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_runtime tests.gateway.delivery.test_proactive_delivery tests.gateway.delivery.test_proactive_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

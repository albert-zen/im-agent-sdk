# Gateway proactive delivery testing

The mirrored proactive suite covers authorization/scope, first-reservation
route pinning, origin/principal namespace isolation, concurrent duplicate
suppression, payload/target conflicts, complete preflight, sticky unknown,
retryable-only resumption and retry-after, partial multi-destination results,
redaction, SQLite restart, and O2 integration.

The in-memory submission repository's atomic reservation/conflict/CAS cases
live in `tests/gateway/persistence/test_memory.py`; this suite consumes that
owner without reintroducing persistence inside delivery orchestration.

Ownership tests additionally prove exact Gateway/contracts facade identity,
absence of both historical implementation modules, and clean package imports.
The mirrored Gateway JSON/CLI ingress suite continues to cover authorization-before-staging,
cancellation join, cleanup, route/result mapping, and loopback CLI policy.
Pure decoded-byte, path-confinement, digest, and staged-content construction
cases live in `tests/interaction/test_media_staging.py`.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_delivery tests.gateway.delivery.test_proactive_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

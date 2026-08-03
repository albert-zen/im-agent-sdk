# Gateway proactive delivery testing

The mirrored proactive suite covers authorization/scope, first-reservation
route pinning, origin/principal namespace isolation, concurrent duplicate
suppression, payload/target conflicts, complete preflight, sticky unknown,
retryable-only resumption and retry-after, partial multi-destination results,
redaction, SQLite restart, and O2 integration.

Ownership tests additionally prove exact Gateway/contracts facade identity,
absence of the historical implementation module, and clean package imports.
The JSON/CLI ingress suite remains at its current path until the focused
ingress move and continues to cover authorization-before-staging, bounded
inline media, cancellation join, cleanup, and loopback CLI policy.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_delivery tests.test_delivery_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

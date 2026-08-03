# Gateway proactive authorization testing

Focused tests cover principal validation, generated and explicit credentials,
the finite credential bound, duplicate rejection, authenticate/revoke
behavior, concurrent registry access, exact facade identity, and removal of
the historical mixed-module symbols.

The proactive-delivery and ingress suites continue to prove that
authentication precedes route resolution/media work, denied scope causes no
Channel side effect, and thread-targeted results do not disclose native
Conversation identity.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_authorization tests.gateway.delivery.test_proactive_delivery tests.test_delivery_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

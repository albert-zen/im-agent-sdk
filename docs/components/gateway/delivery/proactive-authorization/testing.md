# Gateway proactive authorization testing

Focused tests cover principal validation, generated and explicit credentials,
the finite credential bound, keyword-only positive non-`bool`
`max_principals` validation, duplicate rejection, authenticate/revoke behavior,
and exact facade identity/removal of the historical mixed-module symbols.

Capacity tests use only opaque credentials as the registry identity. They
prove that a full registry still authenticates and revokes existing
credentials; duplicate issuance retains the duplicate error before the
capacity error; a distinct credential receives the one fixed redacted capacity
`ValueError` with no registration; and revoking one credential frees exactly
one slot for reuse. Final-slot races cover both distinct credentials (exactly
one admission and one capacity failure) and the same credential (one admission
and one duplicate failure). Cancellation while waiting for the registry lock
must leave no partial entry, and a fresh authorizer proves the intended
process-local restart reset.

The ownership assertions require `DeliveryAuthorizer`, `DeliveryPrincipal`,
and `validate_delivery_principal` to have the Gateway authorization module as
their implementation owner. The `imagent.gateway.delivery`,
`imagent.adapters`, and `imagent.contracts` names must remain the same objects
as that owner; compatibility does not permit a second definition.

The proactive-delivery and ingress suites continue to prove that
authentication precedes route resolution/media work, denied scope causes no
Channel side effect, and thread-targeted results do not disclose native
Conversation identity. Capacity adds no Gateway limits setting, persistence,
diagnostic credential data, hook, or alternate delivery path.

Run:

```sh
PYTHONPATH=src python -m unittest tests.gateway.delivery.test_proactive_authorization tests.gateway.delivery.test_proactive_delivery tests.gateway.delivery.test_proactive_ingress -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Also run every repository gate in `AGENTS.md`.

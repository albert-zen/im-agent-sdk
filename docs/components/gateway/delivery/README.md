# Gateway delivery components

Gateway delivery converts one logical outbound message into deterministic
Channel-compatible work, executes it through bounded destination lanes, and
records only bridge identity/evidence needed for safe convergence.

## Leaves

- [planning design](planning/design.md) and
  [testing](planning/testing.md) — pure deterministic capability planning.
- [coordination design](coordination/design.md) and
  [testing](coordination/testing.md) — bounded destination-ordered execution
  and conservative receipt aggregation.
- [outcome observation design](outcome-observation/design.md) and
  [testing](outcome-observation/testing.md) — bounded best-effort O2
  notification after one logical delivery attempt.
- [submissions design](submissions/design.md) and
  [testing](submissions/testing.md) — immutable proactive-delivery identity,
  destination snapshots, and durable typed outcomes.
- [proactive authorization design](proactive-authorization/design.md) and
  [testing](proactive-authorization/testing.md) — opaque credential to typed
  stable delivery scope.
- [proactive delivery design](proactive-delivery/design.md) and
  [testing](proactive-delivery/testing.md) — authorized, route-pinned,
  idempotent delivery through the common bounded path, including finite
  process-local exact-delivery-ID ingress coordination before authorization or
  staging. Its contract seam in `proactive.py` is the sole owner of the typed
  vocabulary and validator, and its orchestration sibling in
  `proactive_runtime.py` remains the runtime owner. Passive submission state
  stays in `gateway.persistence.state_contracts` without a reverse dependency.

JSON proactive ingress remains Gateway-owned. The optional `imagent-send`
client is owned by the Interaction [client-tools leaf](../../interaction/client-tools/design.md).
Delivery owns no local client argument/file encoding or response presentation,
native encoding, durable job, content storage, route policy, or checkpoint
authority.

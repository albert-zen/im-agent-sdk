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
  idempotent delivery through the common bounded path. Its contract seam in
  `proactive.py` and orchestration sibling in `proactive_runtime.py` are one
  leaf; the next focused #216 slice may move the typed vocabulary into that
  seam without making runtime depend on a reverse contract cycle.

JSON/CLI proactive ingress remains mapped to its current authoritative
documents until its focused move. Delivery owns no native encoding, durable
job, content storage, route policy, or checkpoint authority.

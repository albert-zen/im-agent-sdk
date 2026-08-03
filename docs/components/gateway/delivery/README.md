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

Proactive delivery/authorization remains mapped to its current authoritative
documents until its own focused move. Delivery owns no native encoding,
durable job, content storage, route policy, or checkpoint authority.

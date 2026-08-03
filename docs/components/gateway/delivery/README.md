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

Submissions, proactive delivery/authorization, and outcome observation remain
mapped to their current authoritative documents until their own focused
moves. Delivery planning/coordination owns no native encoding, durable job,
content storage, route policy, or checkpoint authority.

# Gateway delivery components

Gateway delivery converts one logical outbound message into deterministic
Channel-compatible work, executes it through bounded destination lanes, and
records only bridge identity/evidence needed for safe convergence.

## Leaves

- [planning design](planning/design.md) and
  [testing](planning/testing.md) — pure deterministic capability planning.

Coordination, submissions, proactive delivery/authorization, and outcome
observation remain mapped to their current authoritative documents until their
own focused moves. Planning owns no native send, retry appetite, durable job,
or content storage.

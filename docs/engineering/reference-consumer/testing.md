# Reference consumer testing

The focused integration test executes the same `run_reference_consumer()`
entry point used by `python -m examples.reference_consumer.main` and proves:

- explicit managed-CWD Project create-and-select and Thread create-and-bind
  return stable public resource references;
- Conversation A discovers and reads the stable Application through the public
  Conversation- and Application-scoped factories, then completes the first
  ordinary round trip before Conversation B binds;
- ordinary text crosses Channel admission, Controller pass-through, binding,
  Application input, one authoritative Thread observer, routing, and Channel
  delivery;
- two Conversations share one Thread without a duplicate subscription;
- switching one Conversation isolates both old- and new-Thread output, and
  switching back restores exactly two destinations without duplicate completed
  delivery;
- closing the first Gateway and SQLite store, then constructing fresh Gateway,
  Channel, registry, and store objects over the same database while reusing only
  the authoritative Application, reconstructs bindings, per-destination routes,
  checkpoints, and terminal workflow receipts;
- one output completed through native Application ingress while the Gateway is
  absent reaches both restored destinations exactly once, without older-output
  duplication, native-input redispatch, or more than one active subscription for
  the stable Thread;
- public binding generations match their pre-close values, active destination
  checkpoints advance from their captured stable item to the missed stable item,
  completed idempotency rows survive, and a duplicate prior stable Channel input
  causes neither another Application call nor another delivery;
- read-only SQLite integrity, complete schema/column allowlisting, and bounded
  type/cardinality/value-shape validation cover every bridge row, while race-bounded descriptor inspection
  covers the main database and every present sidecar and rejects exact and
  supported encoded/compressed forms of transcript, native payload, request-body,
  media, artifact, credential, and workspace-path sentinels;
- adversarial acceptance tests inject UTF-16, hex, and compressed BLOBs,
  fragmented TEXT rows, WAL-only sentinel evidence, and a file-growth race;
  every case must fail closed;
- public create-and-bind and observe results activate their committed route
  before any later inbound input, while retained surfaces reject after stop;
- selected common, neutral read-only, and neutral effectful commands use one
  frozen local registry, while duplicate registration and an unfrozen registry
  fail before input acceptance;
- a no-Controller composition treats Slash-looking text as ordinary input;
- diagnostic values and executable stdout remain finite and omit scenario
  content, paths, scoped resource identities, and free-form errors; and
- shutdown leaves no active local subscription, adapter, store lease/resource,
  registry work, or scenario-owned task.

Focused lifecycle counterexamples additionally force concurrent starts,
stop/cancellation during blocked startup, inner-runtime construction failure,
live renewal loss, and renewal loss during blocked Channel startup, proving
every path closes the one acquired store and leaves no live adapter authority.
They also force partial-start and normal-stop cleanup failures across Channel,
Controller, and Application owners, plus pre-acquisition validation failures
for all limit categories, invalid Application capabilities, malformed
Channel/store/session structure, and mutable identity drift.
A one-worker-capacity switch also proves replay of
an older terminal bind remains the exact stored success without changing the
newer Conversation binding. The same capacity bound proves create-and-bind can
hand the sole worker slot from the previous Thread to the new Thread, including
caller cancellation after the durable commit but before D-owned route
reconciliation finishes.

Structural tests reject example imports from private Gateway, adapter, effect,
store-session, repository, claim, or checkpoint modules. They also prove the
wheel contains all four canonical modules and no second executable consumer.

Run focused source-tree evidence with:

```sh
PYTHONPATH=src:. uv run python -m unittest tests.gateway.test_reference_consumer -v
PYTHONPATH=src:. uv run python -m examples.reference_consumer.main
```

Then build exactly one wheel and run the repository clean-install smoke. Its
base case executes the installed module from outside the repository without a
`PYTHONPATH`; success from an editable source checkout is not accepted as wheel
evidence.

Finally run the complete repository gates in `AGENTS.md`, AgentKit `check` and
`review-guidance`, and the required clean-context review/fix/re-review loop.

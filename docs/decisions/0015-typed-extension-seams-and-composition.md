# ADR 0015: Typed extension seams and composition boundaries

- Status: Accepted
- Date: 2026-08-03
- Issues: #31, #33, #34, #39, #40, #42

## Context

The IMCodex and IMZen Gateway migrations proved several legitimate consumer
needs at different points in the one Channel-to-Application bridge path:

- adapt verified inbound content for one Application without replacing
  Channel admission or Gateway routing;
- present a classified inbound failure without reopening an unsafe native
  input attempt;
- normalize or materialize application-specific presentation facts inside the
  existing live/history path;
- apply destination-specific visibility after projection selects a route;
- observe one completed logical delivery so a consumer can release transient
  resources.

Each need can remain consumer or adapter policy, but adding an unrelated
callback and constructor argument for every request would make Gateway an
untyped middleware host. The issues also repeat the same safety requirements
with subtly different wording: bounded work, stable identity, explicit
failure, no socket-path blocking, no checkpoint or binding authority, and no
second event/history stream.

The SDK needs one governance model for extension seams without pretending that
a transformer, presenter, delivery observer, native adapter hook, and startup
validator have the same effects or failure semantics.

## Decision

### The bridge path has named extension positions

An extension has exactly one documented position and one typed responsibility:

```text
Channel verify/access
  -> durable inbound admission and media preparation
  -> optional Controller
  -> exact complete binding or typed pre-acceptance failure
  -> [I1] inbound content transformer
  -> route preparation and default prefer-active-Turn input
  -> Application dispatch
  -> [I2] classified inbound failure presenter

Application native event/history
  -> [A1] adapter-owned normalized presentation/materialization
  -> canonical Agent event/history
  -> projection destination resolution
  -> [O1] per-destination outbound presentation policy
  -> delivery planning and one logical coordinated attempt
  -> [O2] post-outcome observer
  -> Channel result
```

Channel startup validation is a separate side-effect-free adapter capability,
not a message-pipeline hook. Read-only adapter diagnostic providers remain
governed by ADR 0014. Preserving already-normalized message Metadata is a Core
fidelity invariant, not an extension seam.

### Every position has a distinct effect contract

- **I1 transformer:** may replace only the verified message's typed content.
  It cannot change Conversation, sender, native message/reply identity,
  admission ownership, binding, client-message identity, continuation
  preference, or dispatch policy. It runs only for Controller-unconsumed input
  after the exact complete binding is resolved and validated, and fails before
  route mutation or Application dispatch. Missing, incomplete, foreign, or
  malformed binding never invokes I1. It is replay-safe for the same stable
  inbound identity: a reclaimable pre-dispatch claim may invoke it again after
  cancellation, failure, or process restart. It therefore cannot use
  invocation as an external side-effect or one-time delivery signal.
- **I2 failure presenter:** receives one fixed origin plus a bounded failure
  classification of `pre_acceptance`, `outcome_unknown`, or
  `post_acceptance`. Gateway, not the presenter, owns the inbound claim
  transition. With no presenter, existing exception/retry behavior is
  unchanged. For `pre_acceptance`, configuring I2 changes the bridge handling
  deliberately: Gateway completes the owned inbound claim before attempting
  the error delivery, making that presentation decision terminal instead of
  releasing the input for native redelivery. `outcome_unknown` retains the
  protected `side_effect_started` claim and `post_acceptance` remains
  terminal. Presenter failure cannot change any of those transitions or grant
  permission to repeat native input.
- **A1 adapter presentation/materialization:** receives bounded typed native
  facts, never a raw protocol envelope. It stays inside the adapter's single
  ordered event/history normalization path. A recoverable association must be
  reproducible from authoritative native history; live-only presentation is
  explicitly non-replayable and never advances a completion checkpoint.
  Materialized bytes, trust, leases, quotas, and durable spool policy remain
  consumer-owned.
- **O1 destination policy:** runs only after a concrete route is selected and
  before planning. It may transform content/presentation or suppress that one
  destination, but cannot change delivery identity or destination. A durable
  suppression completes the existing outbound idempotency claim before the
  route checkpoint compare-and-swap. It is therefore a durable completed
  projection decision, not a successful Channel delivery. Recovery may use
  the already-completed stable delivery ID to converge a lagging checkpoint,
  so intentionally hidden output is not replayed forever. A crash before
  idempotency completion may reevaluate the policy because no Channel side
  effect occurred; after completion it is not reinvoked. Live-only output
  never advances that checkpoint. This explicitly updates the delivery-only
  checkpoint wording in ADR 0007 while preserving the same stored shape and
  crash-convergence ordering.
- **O2 outcome observer:** receives the original logical delivery context and
  its final typed receipt or bounded execution error after all segments for
  that attempt finish. It is not invoked per segment. Its failure is reported
  through bounded diagnostics but cannot rewrite, retry, delay cleanup owned
  by the delivery runtime, or otherwise change the completed result. O2 is
  best-effort process-local notification, not a durable outcome subscription:
  a crash after delivery completion may lose it, completed recovery does not
  replay it, and a later distinct logical attempt may invoke it again. A
  consumer needing crash-safe cleanup owns a bounded ledger or startup sweep.

Protocols at these positions remain separate even when one consumer object
implements more than one of them. There is no public `PipelineHook`, stage
enum callback, raw event callback, or mutable context bag.

### Shared invocation invariants

Every admitted seam must satisfy all of these rules:

1. Inputs and outputs are typed, immutable, bounded, and free of raw native
   envelopes, credentials, arbitrary diagnostic text, and implicit filesystem
   authority.
2. Stable SDK/native identity, not text or time, defines an invocation. A
   process attempt invokes one position at most once for that stage's
   documented logical attempt. I1 may repeat only while native dispatch is
   still proven absent; A1 follows native live/history recovery; O1 follows
   outbound idempotency completion; O2 is never durably replayed. The SDK does
   not claim global exactly-once callbacks.
3. Work never runs on a Channel or Application socket read callback. Async
   work has finite admission and lifetime, or explicitly inherits an existing
   bounded ordered lane whose overflow/recovery behavior is documented.
4. Absence preserves current behavior. An extension cannot become necessary
   for a valid Channel, Application, or consumer that does not need it.
5. The extension receives no mutation surface for bindings, routes,
   checkpoints, request/Turn correlations, Application truth, Channel
   credentials, or native retry decisions.
6. Failure semantics are stage-specific and explicit. Shared helpers may
   implement timeout, cancellation, exception classification, and diagnostic
   counting only when the affected stages have the same result semantics.
7. Diagnostics retain only fixed categories and bounded counters. Consumer
   exception text, identities, content, paths, and callback return values are
   not retained.

ADR 0012 remains authoritative for input continuation: Gateway supplies
`prefer_active_turn` by default, adapters truthfully return `started` or
`steered`, and no inbound extension may select queueing, replace the expected
Turn, or retarget its immutable reply correlation.

### Composition is grouped by ownership

Before admitting more Gateway seams, composition introduces immutable
owner-scoped groups for repositories, runtime limits, and optional
extensions. New consumer seams enter the extension group rather than adding
top-level Gateway constructor parameters. Existing call sites are migrated as
one behavior-preserving refactor before stage implementations are merged.

Application presentation/materialization stays on its concrete Application
adapter configuration. Channel validation stays on a separate structural
Channel capability. Neither is moved into the Gateway extension group merely
to reduce parameter count.

The groups organize construction; they do not create a service locator.
Extension implementations still receive only the minimum typed stage input,
not Gateway, repositories, or other extensions.

### Admission and evidence remain local

This ADR does not pre-admit every named seam into Core. A public seam still
requires a real consumer, a counterexample, tests for the default-absent path,
and the classification required by ADR 0006. Reuse by another consumer can
later justify an optional common capability; lack of reuse remains evidence
for removal or keeping the mechanism adapter-local.

Each implementation issue and PR names one stage, its evidence consumer, its
failure/idempotency rule, and its default counterexample. One PR must not
combine unrelated stages, diagnostics, native capabilities, or Core fidelity
fixes.

## Classification

- The single authoritative bridge path, stable identity, truthful failure,
  bounded execution, default compatibility, and checkpoint/claim protection
  are Core invariants.
- The typed positions are neutral extension mechanics admitted only with
  evidence.
- Native item mapping, artifact candidate extraction, and startup validation
  are adapter-specific mechanics/capabilities.
- Visibility choices, file-manifest wording, artifact storage, error wording,
  cleanup policy, retry appetite, and operator UX are consumer policy.

## Consequences

- Gateway gains a reviewable extension model rather than a callback list or
  general middleware system.
- IMCodex and IMZen can preserve product behavior without parallel Channel
  admission, raw Application subscriptions, transcripts, or delivery paths.
- Stage-specific protocols remain slightly more verbose than one generic
  callback, but reviewers can reason about side effects, retries, checkpoints,
  and cleanup independently.
- The current combined consumer-projection draft must be split into one PR per
  stage or independent invariant. Existing focused drafts must be rebased onto
  this decision and demonstrate its stage-specific requirements.

## Verification

The composition refactor and every stage implementation must prove:

- current behavior with no extensions configured;
- exact invocation position and identity;
- bounded capacity/lifetime and cancellation/join behavior;
- stage-specific exception, idempotency, checkpoint, and claim outcomes;
- no socket-read-path callback work or second native event subscription;
- fixed redacted diagnostics for extension failure/overflow; and
- cross-Application or cross-Channel counterexamples required by ADR 0006.

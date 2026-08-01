# ADR 0012: Input continuation and reply correlation

- Status: Accepted
- Date: 2026-08-02
- Issues: #14
- Pull request: #22

## Context

An IM Conversation can submit input while its bound native Thread already has
an active Turn. Codex App Server can continue that Turn with `turn/steer`, while
T3 and the currently evidenced Zen surface accept a new native Turn. The SDK
needs one input contract without pretending those native protocols are
identical.

The prior result carried only an accepted Turn ID. When two Conversations sent
input to the same Thread and a Codex steer reused the active Turn ID, the later
`put` replaced the original Conversation/reply target. Native events for the
one Turn could then reply to whichever IM input happened to finish last. A
post-dispatch check cannot repair that mutation safely.

## Decision

The Application input Port accepts an `InputContinuationPreference`. Its
default is `prefer_active_turn`. This is an SDK preference, not a promise that
every native Application has a steer method. Every adapter returns the actual
`started` or `steered` disposition:

- Codex may map the default preference to native `turn/steer` when an active
  Turn is observed. Its concrete adapter can still disable that optional
  native capability for a deployment.
- T3 and Zen currently map the preference to `started`; they must not invent a
  native continuation operation without independent evidence.
- `start_new_turn` explicitly requests the start path where a consumer needs
  it.

Before the native side effect, every adapter invokes a typed dispatch callback
with the planned disposition and correlation policy. `started` uses
`create_new`; `steered` uses `preserve_existing` and names the expected active
Turn. The callback is the last bridge check before dispatch, after adapter
validation and native candidate discovery.

Gateway permits `preserve_existing` only when a correlation for that exact
Thread/Turn already exists. A steer from another Conversation therefore keeps
the original reply destination. If no correlation exists, the callback fails
before native dispatch. The SDK does not retarget an active Turn merely because
another Conversation steered it; any future retargeting would require an
explicit, documented consumer policy evaluated before dispatch.

Turn-correlation repositories are create-only. Repeating an identical value is
idempotent; reusing the same Thread/Turn key with different immutable fields is
a conflict. SQLite never uses an upsert to replace the destination.

After dispatch, the returned disposition and policy must match the authorized
plan. A steered response must return the expected native Turn ID. A mismatch is
a post-acceptance degradation: native authority is preserved, the existing
correlation is not changed, and automatic input retry remains forbidden.

## Classification

- Default continuation preference and truthful `started`/`steered` results:
  common SDK Application Port contract.
- Immutable per-Turn reply destination and pre-dispatch authorization: Core
  bridge invariant.
- Native `turn/steer`, active-Turn discovery, and deployment disablement:
  optional Codex adapter capability/policy.
- Retargeting, fallback UX, and product retry messaging: consumer policy.

This common contract is exercised by Codex steer plus T3 and Zen start
behavior. It does not claim that T3 or Zen implement the Codex wire method.

## Consequences

Application adapter implementations must accept the continuation preference,
invoke the dispatch callback exactly once immediately before a native input
mutation, and return the actual disposition. Legacy duck-typed adapters are
not silently accepted because skipping the callback would reopen the
pre-dispatch race.

The bridge persists no extra transcript, event log, or Turn status. It retains
only the existing bounded, minimal reply correlation. Native Application state
remains authoritative.

## Verification

Tests cover:

- default Codex continuation and explicit start-new mode;
- T3 and Zen truthful `started` fallback;
- two Conversations targeting one Thread/Turn without destination overwrite;
- a steer racing correlation creation and failing before native dispatch;
- native replacement/mismatch after steer as post-acceptance degradation;
- create-only conflict behavior in memory and SQLite;
- SQLite restart preserving the original destination; and
- App Server unknown-outcome behavior for both start and steer.

# IMCodex consumer follow-up blockers

Status: App Server input slice implemented; review and remaining slices pending

This note records the SDK-side gaps found while attempting the downstream
IMCodex migration from SDK merge commit
`c91fe8c35d714b4a325523a3240ad56163a90e65`. It prevents the consumer from
papering over adapter or Core behavior with a second local implementation.

The follow-up branch was created in an independent fresh clone, not an
IMCodex or developer SDK worktree. A fresh `origin/main` fetch, `HEAD`, and an
ancestor check all resolved to the merge commit above. The unchanged SDK
baseline was `251` unit tests passing.

## Boundary decisions

| Gap | Classification | Second integration check | Required owner-side result |
|---|---|---|---|
| Ambiguous native input dispatch | Core invariant with adapter-specific transport evidence | T3 has native command/message IDs; App Server has no input idempotency key | Every App Server input mutation, including active-Turn steer, reports `ApplicationInputOutcomeUnknown` after dispatch instead of authorizing retry. |
| Active-Turn continuation | Common SDK preference with optional native adapter capability | Zen shares the App Server transport but has no independent steer evidence; T3 uses a different native orchestration API with no `turn/steer` | SDK defaults to `prefer_active_turn`; every adapter returns `started` or `steered`. Codex enables native steer by default, while Zen/T3 truthfully start. Gateway authorizes `preserve_existing` before dispatch and never retargets the original Turn reply correlation. |
| Local-image connection epoch | App Server adapter-specific capability wiring | Remote/upload-based Applications do not share a local filesystem epoch | The adapter passes only the client-proven epoch to `turn/start` or `turn/steer`; connection changes fail closed before dispatch. |
| Generic local files | App Server capability plus consumer encoding/exposure policy | T3 has its own media/data encoding; a remote upload Application must not expose a host path | Default to explicit unsupported while App Server has no native file input. The adapter may validate a configured root and bounds, but whether an absolute path may be exposed and the prompt/encoding template require an explicit downstream callback or policy. |
| Quoted native message context | QQ adapter-owned optional capability | Only QQ currently has positive native quote parsing evidence; Telegram, Feishu, and a text webhook are counterexamples without this contract | Preserve a bounded QQ-owned native-untrusted snapshot through the public adapter without widening Core or trusting caller-forged metadata. |
| Deduplication before media work | Core/Channel runtime invariant | Every media-capable Channel can receive provider redelivery | Durable admission must precede attachment download/materialization. A process-local set is only an optimization. |
| App Server tool/system/artifact projection | App Server adapter-specific event/history capability | T3 activities and Codex items have different native shapes | Preserve the native items needed for declared visibility and artifact delivery without a consumer raw-notification side channel. |
| Subscriber/startup/acceptance buffering | Core runtime safety; capacity and overflow UX are consumer policy | Codex, Zen, and T3 can all outpace a slow Channel | Bound every internal accumulation point or define an explicit overflow/reconciliation path before removing the consumer's bounded stage. |
| Adapter diagnostics/health | Optional adapter capability plus consumer presentation policy | Every long-lived native Channel/Application needs operability; products may render health differently | Expose bounded non-secret adapter facts; IMCodex keeps its `health.json` and event UX. |

## Cutover rule

The downstream pin must continue to reference the last merged SDK `main`
commit. No IMCodex Gateway, Codex Application, or public native Channel
cutover is allowed while its corresponding regression test is red. SDK fixes
are merged first; IMCodex then updates its immutable full-commit pin and reruns
the original baseline matrix.

Consumer-owned durable outbox content, managed artifact spool lifetime,
per-artifact acknowledgement, product commands/configuration/Full Access,
Windows launchers, observability presentation, and an explicitly documented
bounded stage compensating for an SDK limitation remain outside SDK Core.

## Reviewable follow-up sequence

The gaps are not one mega-PR. Each slice starts from the latest merged
`origin/main`, owns its tests and documentation, passes CI and clean-context
review, and merges before the next slice is based:

1. **App Server input correctness:** steer dispatch-unknown classification,
   default prefer-active continuation with TOCTOU-safe correlation
   authorization, truthful started/steered results, local-image epoch wiring,
   and explicit unsupported behavior for generic files until a
   concrete downstream encoding/exposure policy justifies a separate seam.
2. **Channel ingress correctness:** QQ-owned quote preservation and an SDK
   Gateway-owned durable admission boundary before expensive media work.
3. **Bounded buffers, projection, and diagnostics:** only the items still
   proven to block downstream cutover, with an explicit overflow/recovery
   design rather than opportunistic queue edits.

IMCodex pins none of the intermediate branch commits. Its dependency advances
only to the final required SDK merge commit present on `main`.

The first slice keeps generic files explicit unsupported rather than adding a
policy seam without a second concrete downstream encoding. It implements the
other three items with focused start/steer, TOCTOU, epoch, and Zen/Codex
counterexample coverage. Channel ingress and bounded projection remain blocked
from downstream cutover until their later independent slices merge.

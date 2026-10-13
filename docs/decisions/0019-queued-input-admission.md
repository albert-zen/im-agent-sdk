# ADR 0019: Queued input admission is separate from execution

- Status: Accepted design; staged implementation, independent review pending
- Date: 2026-10-13

## Concrete counterexample

Zen can commit a canonical human `user_message_queued` while another Turn is
still active. That commit has an item/client identity but no new Turn. Returning
an `AcceptedTurn` or waiting for execution would either invent native truth or
hold admission indefinitely. This is optional Application capability, not a
second Gateway queue or a change to ordinary soft-steer defaults.

## Contract

Keep `AcceptedTurn`, `ApplicationInputDispatch`, and their started/steered
semantics unchanged. `QueuedInputDispatch` declares the exact Thread, original
client message ID, and prepared input SHA-256 before native mutation.
`QueuedInputReceipt` carries those stable identities plus the real queued item
ID and `queued`, `admitted`, or `cancelled` state. Only admitted has a real
same-Thread Turn. Explicit unions represent the plan and acceptance families.

`InputContinuationPreference.QUEUE` is explicit. Unsupported adapters reject
before mutation rather than interpreting it as start-new. The default remains
`PREFER_ACTIVE_TURN`. Gateway integration supplies one immutable typed
ordinary-input configuration; commands and approvals remain separate.

Zen queue input requires `queued-input-admission-v1` and, for images,
`inline-image-input-v1` from `zen/initialize.capabilities` in the dispatch
connection epoch. The native result adds `admission` with state queued/admitted,
clientId and queuedItemId; admitted also carries the original turnId. A missing
receipt after dispatch is unknown. A user-message-only duplicate without real
queued lineage is a mode/idempotency conflict, never a guessed queued receipt.

Native wire `image_bytes` carries explicit mediaType and dataBase64. Host-owned
import/validation produces the existing content-addressed AttachmentRef before
canonical queue admission. Rooted SDK bytes are acquired once; local paths,
implicit start/steer uploads, and a new media/queue ledger are forbidden. Existing
Core single-image/dimension limits apply. Native adopts the SDK's 16-image and
32 MiB decoded-image aggregate limits as explicit new budgets when the envelope
contains inline image bytes; text remains under the transport boundary. Existing
canonical-only native envelopes retain their previous limits.

## Recovery and order

Canonical human queued, user-message and queue-cancelled Items establish pending,
original admitted Turn and cancellation. Host/Agent items with the same client
ID do not establish human input. Complete native snapshots are finite and scope
checked; a failed, partial, oversized or absent read stays unknown and does not
permit resend. Snapshot read never resumes queue execution.

`queue_failed` is live attempt feedback, not a canonical Item or terminal
cancellation. Restart may recover pending identity but cannot recreate an old
failure reason. Native duplicate admission only reads evidence; explicit user
Resume is required after failed startup. Newly committed queue input returns its
mutation-time receipt without waiting for startup/approval/model execution. A
later event may overtake that response; canonical receipt merging is monotonic.

## Gateway delivery dependency

The adapter/model slice was reviewed independently. The Gateway follow-on uses
an additive lease-fenced original correlation linked atomically to the existing
inbound claim, delayed real-Turn correlation, original-IM-identity read/cancel,
bounded separate queue observation, cross-lane request ordering and startup
reconciliation before projection. [The focused Gateway authority](../components/gateway/input/queued-admission/design.md)
defines its ownership. Default started/steered paths remain source-compatible.
No second scheduler, transcript, content persistence or fallback dispatch exists.

Before release, prove real production Gateway/client + authenticated native Zen
with synthetic model/channel and disposable SQLite: both event/response orders,
commit/lost acknowledgement/restart, duplicate/mode conflicts, image identity,
queue_failed/explicit Resume, cancellation, scope/generation/lease fencing,
backpressure/stop/reset, and original-Turn approval/final reply routing.

### Recoverable input fingerprint

The receipt carries the original queue-admission payload fingerprint, not content.
Hash domain is UTF-8 `imagent:zen-queued-input:v1` followed by one NUL byte, then
UTF-8 canonical JSON (sorted object keys, no extra separators/whitespace, no
Unicode normalization) of the ordered typed-part array. Text parts preserve
exact text and boundaries. Image parts normalize to `type=image`, lowercase
actual byte `sha256`, exact closed `mediaType` and integer `byteLength`.
Acquired inline bytes and canonical AttachmentRef therefore yield the same
digest; path, base64 spelling and derived dimensions are excluded. Changed
order, text boundary, bytes, MIME or length changes identity. Recovery always
hashes the original `user_message_queued.input`, even after an edit or admission
whose execution content differs. The Gateway receipt owner compares
this evidence with its original pre-dispatch fingerprint; it does not reopen
input or create a new receipt when the native queue is edited.

### Default typing and ambiguous acknowledgements

The historical `ApplicationInputDispatchHandler` remains a callback for the
ordinary `ApplicationInputDispatch`. New dynamic/queue adapter-neutral callers
use `ApplicationInputPlanHandler` and narrow the acceptance union. The public
`AgentApplicationAdapter` overload for omitted or literal ordinary continuation
preserves `AcceptedTurn`; this compatibility is not limited to concrete Zen
callers. Concrete Zen retains its more precise literal-QUEUE receipt overload.

A dispatched successful RPC with null/array/scalar/malformed-object result is
an ambiguous acknowledgement, never a pre-dispatch rejection. Both production
transport result normalization and the adapter's client boundary validate shape
and raise `ApplicationInputOutcomeUnknown` without a retry or fallback start.

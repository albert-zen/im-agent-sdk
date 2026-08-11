# Gateway effect execution testing

Focused tests prove the closed outcome union and fixed error vocabulary,
stable domain-separated action/payload/phase fingerprints, same-ID replay,
changed-payload conflict, and no retained raw payload.

The executor matrix covers store-only success/conflict/capacity, primitive
native known failure/success/unknown, request-response category parity, both
create-binding workflow families, cancellation at every phase, terminal
replay, known-result binding resume, stale partial, and optional evidenced
reconciliation without blind retry or negative-lookup inference.
Preflight tests prove the callback sees `reserved`, runs before the first
native fence for primitive and workflow mutations, terminalizes a closed
rejection, and is skipped on terminal replay even when current authorization
or request-correlation state has changed. Invalid callback/reconciliation
values after the fence become sticky unknown instead of raising.
Store-preflight parity tests prove Memory and SQLite check a lease-fenced
terminal receipt first, run a new callback before any receipt or bridge-state
write, atomically terminalize a closed rejection without mutation, and replay
both success and rejection without consulting changed resource state.
The route-preparation replay seam proves an identical terminal value is read
without mutation before process-local activation, changed payload remains a
typed conflict, and absence remains `None`; stopped-runtime classification is
then checked at the scoped-action composition boundary.
Memory/SQLite parity also pauses on both sides of the optional commit fence:
shutdown before entry yields typed stale failure with no receipt or route,
while a transaction already inside completes before shutdown; terminal replay
bypasses changed preflight/fence state and cancellation leaves no mutation.
Public Memory/SQLite workflow coverage additionally proves a known native
Thread result cannot commit its foreground binding/route after shutdown wins;
restart resumes that exact result, while a transaction that already owns the
fence completes before shutdown.

The block-C seam is checked structurally: the protocol and requests expose no
store, session, lease, repository, adapter, credential, `Any` context, product
command, or authorization decision. C owns the operation mapping and passes
only validated fixed facts and callbacks.

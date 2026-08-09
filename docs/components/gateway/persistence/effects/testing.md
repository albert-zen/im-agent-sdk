# Gateway persistence effect contracts testing

Tests prove stable domain-separated identity, principal/Gateway/Conversation
namespace separation, changed-payload detection, signed-64-bit and bounded
UTF-8/tuple admission, exact phase derivation, closed categories/phases/error
codes/reference kinds, complete reference hierarchy validation, and exact
outcome codec round trips.

Malformed persisted shapes fail closed. Tests also inspect serialized values
to ensure they contain no raw principal, action arguments, content, path,
credential, callback, exception text, or native execution truth.
Plan tests reject cross-Conversation bindings/routes and prove route-only plans
need no synthetic binding target. Error tests admit only the closed common
operation-error vocabulary, and codecs reject every unknown JSON member.
Plan tests also cover every `BindingClearScope`, reject simultaneous
replacement/clear intent, and prove hierarchical clears require no caller-side
binding read.
Generation tests reject Boolean, negative, floating-point, and textual values
uniformly in effect values, mutation preconditions, receipts, and persisted
decoding. Executor parity tests prove an invalid post-fence value is recorded
as sticky unknown on its first result and every same-ID replay.

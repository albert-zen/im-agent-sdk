# Gateway projection request correlation testing

Request-correlation conformance must prove:

- an accepted Turn stores immutable origin/reply identity; concurrent Turns do
  not inherit another inbound message's reply target;
- a steer requires the expected existing correlation before native dispatch and
  cannot retarget the Turn, including after SQLite restart;
- post-acceptance correlation failure keeps the inbound claim terminal, while
  an unknown native outcome remains side-effect-started rather than retryable;
- a request correlation is created only for a destination whose request
  delivery accepted or completed, and one failed destination does not block a
  successful one;
- an immediate same-destination response at accepted receipt but before route
  persistence waits for publication in both preflight and direct routing;
- a held unrelated destination does not block an already authorized response,
  and late completion inherits the request-wide terminal state;
- failed, unknown, cancelled, and stopped publication never grants authority;
  cancelling a waiter leaves publication owned by its existing lifecycle;
- synchronous publication callbacks and parent-await-child cycles return bounded
  delivery-pending outcomes when the configured wait expires first, while
  independent children wait and succeed;
- pending deadlines are finite, configurable and capped by native expiry;
  a completed pending failure stays terminal and does not execute later;
- native expiry may cancel Channel.send and its awaited direct/child response
  using unchanged cancellation semantics. A gated slow preflight proves
  RESERVED/NULL without native dispatch; after publication cleanup, explicit
  same-ID retry in the same runtime or fresh SQLite Gateway rejects expired
  requests, records terminal stale, and never resends or dispatches them;
- caller/stop cancellation remains cancellation, not a fabricated pending
  outcome. Existing receipt recovery applies if cancellation interrupts failure
  persistence; startup never automatically replays a reserved response;
- public SQLite composition proves the same race, foreign rejection, exactly
  one native effect, held Channel cancellation and fresh-object authoritative
  pending recovery without a second Channel send; and
- only a delivered destination may submit a typed response; local contenders,
  stale/resolved state, and a second response fail explicitly;
- state transitions are request-wide, atomic, monotonic, and same-state
  idempotent; late destination completion cannot reopen authority;
- request/response retention, cardinality, choice/question limits, and native
  epoch identity are finite; no correlation persists prompt or answer content;
- a terminal native request event resolves correlations without ending the
  Turn; and
- reconnect recovers pending requests only from an authoritative snapshot and
  otherwise records truthful degraded/stale state without manufacturing a
  pending request.

Focused contract, validation, runtime, Turn-correlation, request-delivery,
request-response, transition, and thread-scoped pending-snapshot evidence lives
in `tests/gateway/projection/test_request_correlation.py`. It also proves exact
public identity in clean processes and the absence of the historical runtime
and `imagent.contracts` attributes. Durable SQLite, restart, generic recovery,
and real Application/Gateway integration evidence remains in
`tests/gateway/persistence/test_sqlite.py`, `tests/applications/adapters/appserver/test_gateway_request_integration.py`,
`tests/gateway/test_operations_integration.py`, and `tests/gateway/projection/test_hardening.py`.
The canonical public acceptance in `tests/gateway/test_reference_consumer.py`
adds two-recipient first-writer, non-recipient preflight rejection, terminal
replay, authoritative pending restart, and no-snapshot stale counterexamples.

Deterministic Event-gated regressions live in
`tests/gateway/projection/test_request_publication.py` and
`tests/gateway/test_request_publication_integration.py`; the gates never sleep
or weaken the production authorization or deadline.

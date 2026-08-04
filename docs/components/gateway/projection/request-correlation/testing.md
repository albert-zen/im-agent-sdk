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

Focused policy evidence lives in
`tests/gateway/projection/test_request_correlation.py`. Durable SQLite evidence
lives in `tests/gateway/persistence/test_sqlite.py`; broader integration
coverage remains in `tests/test_appserver_requests.py` and
`tests/test_projection_hardening.py`.

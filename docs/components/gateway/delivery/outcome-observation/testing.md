# Gateway logical delivery outcome observation testing

Focused tests mirror the owner at
`tests/gateway/delivery/test_outcome_observation.py` and prove:

- absent observer leaves delivery/result/diagnostics unchanged;
- accepted, rejected, retryable, partial, unknown, execution-error, and
  cancellation outcomes remain typed;
- one multi-segment/internal-retry attempt produces one notification;
- later resumed destination attempts are distinct;
- preflight, replay, recovery, and suppression create no false notification;
- notification begins only after Coordinator cleanup and destination-state
  persistence attempt;
- bounded item/string/metadata facts reject invalid or excessive input;
- capacity, timeout, cancellation overrun, and consumer failure affect only
  fixed diagnostics;
- close/cancellation remain finite and cannot delay delivery cleanup;
- public values from `imagent.gateway.delivery` are exact owner objects; and
- the historical `imagent.delivery_outcomes` module is absent.

Proactive, projection, Coordinator, diagnostics, and artifact-lifetime suites
continue proving that O2 cannot alter receipt, retry, checkpoint,
idempotency, restart convergence, or cleanup ordering.

The reference artifact ledger is the public consumer: accepted, rejected, and
unknown destination notifications release only its own leases, while replay
does not fabricate a second observation or cleanup attempt.

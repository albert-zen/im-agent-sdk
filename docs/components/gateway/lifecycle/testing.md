# Gateway lifecycle testing

Current lifecycle evidence is spread across
`tests/gateway/test_operations_integration.py`, `tests/gateway/test_vertical_slice.py`,
`tests/gateway/test_diagnostics.py`, and startup races in the inbound/projection suites.
The target exact-owner suite is `tests/gateway/test_lifecycle.py`. Until the
package-root `ImAgentGateway.start()`/`stop()` orchestration gap is moved, the
focused suite must also prove the lifecycle helper owner and exact facade
identity without changing that orchestration.

Tests must prove:

- canonical `Gateway` owns one renewable coherent-store lease, creates scoped
  actions only while running, rejects private repository/executor inputs, and
  joins the renewal task plus session/store close during async-context exit;
- renewal begins before inner startup can overrun the first lease; renewal loss
  cancels blocked startup, closes live admission, projection, adapters, session,
  and store, and is surfaced by `wait_closed()`;
- construction failure after lease acquisition closes the session/store, and
  retained scoped surfaces fail before reaching stopped Applications;
- same-instance concurrent starts join one serialized transition and one lease;
  stop racing a blocked startup cancels and joins its rollback; cancellation
  while entering the async context closes every partially started owner once;
  validation failure permits one corrected retry without acquiring or closing
  another transition's store, and renewal loss racing explicit stop closes all
  owners once without deadlock or a second cleanup;
- startup ordering installs restored observation before live delivery and
  keeps startup admission active through FIFO drain;
- FIFO capacity, ordering, sticky overflow, diagnostics, and reset are finite;
- every startup failure closes admission before awaits, releases only matching
  owned pre-side-effect claims, and leaves no task or queued message behind;
- Channel callbacks racing overflow, rollback, failed startup, or shutdown
  cannot reach Controller or Application work;
- partial startup stops only successfully started owners in reverse order and
  preserves the primary failure when cleanup also fails;
- all SDK-owned Channels receive their exact admission handler through one
  two-argument start invocation; a legacy one-argument body runs zero times,
  and an internal two-argument `TypeError` runs once;
- failed Channel startup closes inbound admission, performs no Controller or
  Application work, stops the current and prior applicable Channels exactly
  once per attempt, and leaves a later restart bounded and explicit;
- normal stop closes projection, each bounded extension/delivery runtime, and
  the accepted Controller lifecycle in owner order, while loose/mixed
  Controller persistence fails before any owner starts;
- normal stop leaves no second dispatch implementation, Application
  subscription, Channel admission path, or stale
  active-Thread observation slot/health entry; a pending accepted-input fence
  remains ordered until its input owner completes rather than being cleared by
  worker cancellation, while the established restore boundary resets its
  process-local acceptance buffers before route recovery;
- scoped route activation racing or following stop is a typed non-success,
  cannot outlive worker termination as false success, and leaves no task,
  bootstrap barrier, route/action lock, or capacity reservation; restart and
  terminal replay restore the one worker and same durable route;
- stop during authoritative route preflight wins the shared commit boundary
  and leaves no route/receipt, whereas stop queued behind an entered Memory or
  SQLite transaction waits for commit and produces a post-durable partial;
- stop after a foreground workflow's native result is known but before its
  binding/route fence writes no route in both built-in stores; restart resumes
  the same workflow without repeating native creation;
- `GatewayStartupAdmission`, `GatewayStartupOverflow`, and
  `GatewayNotRunning` have one owner in `imagent.gateway.lifecycle`, while
  importing the removed `imagent.gateway_startup` module fails explicitly.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_reference_consumer tests.gateway.test_operations_integration tests.gateway.test_vertical_slice tests.gateway.test_diagnostics -v
```

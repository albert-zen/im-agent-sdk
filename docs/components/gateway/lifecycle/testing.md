# Gateway lifecycle testing

Current lifecycle evidence is spread across
`tests/gateway/test_operations_integration.py`, `tests/gateway/test_vertical_slice.py`,
`tests/gateway/test_diagnostics.py`, and startup races in the inbound/projection suites.
The target exact-owner suite is `tests/gateway/test_lifecycle.py`. Until the
package-root `ImAgentGateway.start()`/`stop()` orchestration gap is moved, the
focused suite must also prove the lifecycle helper owner and exact facade
identity without changing that orchestration.

Tests must prove:

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
- `GatewayStartupAdmission`, `GatewayStartupOverflow`, and
  `GatewayNotRunning` have one owner in `imagent.gateway.lifecycle`, while
  importing the removed `imagent.gateway_startup` module fails explicitly.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_operations_integration tests.gateway.test_vertical_slice tests.gateway.test_diagnostics -v
```

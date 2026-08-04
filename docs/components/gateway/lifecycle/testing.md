# Gateway lifecycle testing

Current lifecycle evidence is spread across
`tests/test_gateway_operations.py`, `tests/test_gateway_vertical_slice.py`,
`tests/test_diagnostics.py`, and startup races in the inbound/projection suites.
The target exact-owner suite is `tests/gateway/test_lifecycle.py`.

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
- normal stop closes projection and each bounded extension/delivery runtime and
  leaves no second Application subscription or Channel admission path.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.test_gateway_operations tests.test_gateway_vertical_slice tests.test_diagnostics -v
```

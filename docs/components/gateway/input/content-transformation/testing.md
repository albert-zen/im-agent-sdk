# Gateway inbound content transformation testing

Focused evidence is `tests/gateway/input/test_content_transformation.py` and
`tests/gateway/input/test_policy_free_ordinary_input.py`.

Tests must prove:

- absence is exact identity behavior and Controller-consumed/duplicate input
  bypasses I1;
- missing/incomplete binding and foreign Conversation/resource ancestry bypass
  I1 before route or native work;
- the transformer receives the original frozen message and can replace content
  without changing any envelope, binding, client ID, continuation, or
  correlation fact;
- only a non-empty tuple of supported bounded content is accepted;
- timeout, exception, invalid output, capacity exhaustion, cancellation, and
  cancellation overrun finish before native dispatch and clean up bounded tasks;
- a safe reclaim/restart may invoke I1 again, while a protected or accepted
  input never gains retry authority;
- diagnostics are finite/redacted and no callback runs on a socket read path;
- I1 failures enter I2 only as `pre_acceptance` fixed facts.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.input.test_content_transformation -v
```

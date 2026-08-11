# Gateway inbound admission testing

Focused evidence is `tests/gateway/test_admission.py`; package-root export
evidence is `tests/gateway/test_package_root.py`; native Channel integration
and preparation ordering remain in
`tests/interaction/channels/adapters/test_native_channels.py`.

Tests must cover:

- stable scope/key derivation and Channel/Conversation identity mismatch;
- duplicate and protected claims rejected before media preparation;
- owner-token fencing across refresh, deliver, release, stale reclaim, and a
  replacement owner;
- prepared message identity mismatch releases the matching claim and never
  transfers work;
- deliver/release are one-shot and concurrent attempts cannot hand off twice;
- failed preparation, cancellation, startup rejection, and shutdown races
  release only proven pre-side-effect ownership;
- every SDK-owned Channel receives the exact supplied admission handler on the
  Gateway path, with no second admission instance or message-only start;
- a legacy one-argument start body is never entered, while an internal
  two-argument `TypeError` is invoked once and remains the primary failure;
- dispatch-fenced state is never reclaimed or reauthorized;
- clean-process absence from the finite `imagent.gateway` facade and removal
  of the historical module, while focused-owner imports retain exact identity.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.gateway.test_admission tests.interaction.channels.adapters.test_native_channels -v
```

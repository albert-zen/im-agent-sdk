# Gateway inbound admission testing

Current focused evidence is `tests/test_inbound_admission.py`; native Channel
integration and preparation ordering remain in `tests/test_native_channels.py`.
The target exact-owner suite is `tests/gateway/test_admission.py`.

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
- dispatch-fenced state is never reclaimed or reauthorized;
- clean-process exact facade identity and removal of the historical module
  after the later mechanical slice.

Run:

```sh
PYTHONPATH=src uv run python -m unittest tests.test_inbound_admission tests.test_native_channels -v
```

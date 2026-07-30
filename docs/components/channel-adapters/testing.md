# Channel adapters testing

Every Channel adapter should prove:

- stable configured Channel and native Conversation identity;
- duplicate inbound delivery does not repeat Agent mutation;
- access checks occur before media work;
- Markdown/plain fallback and ordered segmentation;
- attachment size/type/source handling;
- native reply behavior and receipt meaning;
- retry behavior when native idempotency is absent;
- reconnect state remains Channel-owned.

Run the reusable Channel contract suite plus native adapter tests:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_imcodex_channels \
  tests.test_adapter_contracts \
  tests.test_gateway_vertical_slice -v
```

After Issue #9 adds local QQ/Telegram/Feishu/Weixin modules, each native module
must receive a focused adapter page and tests before its path is added to the
mapping. Current config does not pretend those not-yet-local files exist.

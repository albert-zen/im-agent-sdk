# Channel adapters testing

Every Channel adapter should prove:

- stable configured Channel and native Conversation identity;
- duplicate inbound delivery does not repeat Agent mutation;
- access checks occur before media work;
- Markdown/plain fallback and ordered segmentation;
- attachment size/type/source handling;
- native reply behavior and receipt meaning;
- typed per-attachment accepted/rejected results and stable attachment
  identity independent of staging path;
- retry behavior when native idempotency is absent;
- reconnect state remains Channel-owned.

Run the reusable Channel contract suite plus native adapter tests:

```sh
PYTHONPATH=src python -m unittest \
  tests.test_native_channels \
  tests.test_channel_qq \
  tests.test_channel_telegram \
  tests.test_channel_feishu \
  tests.test_channel_weixin \
  tests.test_adapter_contracts \
  tests.test_gateway_vertical_slice -v
```

`test_package_independence.py` additionally guards package metadata, lockfile,
CI, and `src/imagent` against reintroducing a consumer-package dependency.
Release validation builds the wheel and constructs every adapter in clean
environments with only its declared extra:

```sh
uv build --wheel
uv run python scripts/smoke_clean_install.py
```

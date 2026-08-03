# Channel adapters testing

Every Channel adapter should prove:

- stable configured Channel and native Conversation identity;
- duplicate inbound delivery does not repeat Agent mutation;
- access checks occur before media work;
- durable completed/in-flight admission rejects restart redelivery before
  attachment preparation;
- a durable in-flight rejection does not let the process-local fast path block
  a later reclaim attempt;
- preparation failure releases only its fenced pre-side-effect lease, while a
  stale preparation owner cannot hand off or release a replacement claim;
- an honest `DeliveryProfile` for Markdown/plain fallback, text length units,
  attachments/grouping, and reply scope;
- native encoding accepts every common-planner segment and defensive direct
  calls do not overstate platform support;
- attachment size/type/source handling;
- native reply behavior and receipt meaning;
- typed per-attachment accepted/rejected results and stable attachment
  identity independent of staging path;
- native retryable/unknown receipt mapping when idempotency is absent;
- reconnect state remains Channel-owned.

The four SDK-owned native Channels additionally prove optional startup
validation with both valid and invalid resolved settings. Preflight must be
repeatable before and after a lifecycle, leave the live native slot empty, and
perform no transport start, worker creation, callback registration, credential
publication, or persistent mutation. Tests also prove the pure validator gates
the same resolved settings later consumed by the native factory and `start()`,
while a conforming fake/third-party Channel without the structural capability
remains valid. Construction of the SDK native wrapper without a validator is a
type and call-shape error rather than a falsely advertised capability.

ADR 0014 diagnostics tests cover lifecycle transitions for all four native
Channels, bounded QQ/Feishu inbound queue facts, configured identity pinning,
provider failure/invalid-shape fallback, and repeated side-effect-free reads.

QQ-only quote fixtures additionally cover direct and group events, missing and
malformed provider fields, every text/field/count bound, ignored nested history
and media URLs, anti-forgery boundaries, and one
native-to-common-to-Application vertical slice. The tests also prove that this
single-Channel feature does not change common capabilities, schemas, Metadata,
or routing/approval identity.

Run the reusable Channel contract suite plus native adapter tests:

```sh
PYTHONPATH=src:tests python -m unittest \
  tests.test_native_channels \
  tests.test_channel_qq \
  tests.interaction.channels.adapters.test_telegram \
  tests.interaction.channels.adapters.test_feishu \
  tests.interaction.channels.adapters.test_weixin \
  tests.test_adapter_contracts \
  tests.test_gateway_vertical_slice -v
```

`test_package_independence.py` additionally guards package metadata, lockfile,
CI, and `src/imagent` against reintroducing a consumer-package dependency.
Release validation builds the wheel and constructs every adapter in clean
environments with only its declared extra; each native clean-wheel case also
runs disabled, side-effect-free startup validation:

```sh
uv build --wheel
uv run python scripts/smoke_clean_install.py
```

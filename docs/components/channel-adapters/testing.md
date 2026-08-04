# Channel adapters testing

Every Channel adapter should prove:

- stable configured Channel and native Conversation identity;
- duplicate inbound delivery does not repeat Agent mutation;
- access checks occur before media work;
- access-denial reporting remains bounded without allowing denied input to
  reach preparation or handoff;
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

Base ownership tests additionally prove that the inherited access, denial
report, inbound handoff, and outbound enforcement methods delegate to the
focused Interaction leaves while native route context and diagnostics remain
adapter-owned. They lock virtual inbound override order and
denial-before-handoff, lazy outbound route/fallback override behavior, explicit
denial event ordering, and propagation of policy-raised `PermissionError`
without a synthetic event or health update.

Facade ownership tests additionally prove that all internal consumers import
Channel contract/admission/receipt values from `imagent.interaction.channels`,
that the exact retired names are absent from `imagent.adapters` and
`imagent.contracts`, and that the retained `imagent.channels` adapter facade
is exact-object identical to its Interaction runtime owner. Unrelated
Application/Gateway/passive-state facade names remain available.

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
Target ownership tests additionally cover the immutable fact shapes, bounded
state transitions, exact canonical Interaction contract identity, and removal
of the historical native diagnostics module.

Ownership tests also require the Interaction runtime objects and formal
`imagent.channels` facade to be object-identical, keep provider imports lazy,
and reject the removed historical `imagent.channels.runtime` path.

QQ-only quote fixtures additionally cover direct and group events, missing and
malformed provider fields, every text/field/count bound, ignored nested history
and media URLs, anti-forgery boundaries, and one
native-to-common-to-Application vertical slice. The tests also prove that this
single-Channel feature does not change common capabilities, schemas, Metadata,
or routing/approval identity.

Run the reusable Channel contract suite plus native adapter tests:

```sh
PYTHONPATH=src:tests python -m unittest \
  tests.interaction.channels.adapters.test_native_channels \
  tests.interaction.channels.adapters.test_qq \
  tests.interaction.channels.adapters.test_telegram \
  tests.interaction.channels.adapters.test_feishu \
  tests.interaction.channels.adapters.test_weixin \
  tests.conformance.test_adapter_contracts \
  tests.test_gateway_vertical_slice -v
```

`tests/engineering/test_release.py` additionally guards package metadata, lockfile,
CI, and `src/imagent` against reintroducing a consumer-package dependency.
Release validation builds the wheel and constructs every adapter in clean
environments with only its declared extra; each native clean-wheel case also
runs disabled, side-effect-free startup validation:

```sh
uv build --wheel
uv run python scripts/smoke_clean_install.py
```

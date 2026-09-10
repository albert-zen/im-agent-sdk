# Reuse and provenance

## Policy

Production adapters should begin by migrating proven code, tests, and fixtures
from mature integrations. Rewriting from memory is the fallback, not the
default.

Every substantial migration records:

```text
source repository
source commit
source paths
license
local modifications
behavioral tests retained or added
```

Copy the smallest coherent adapter boundary. Do not copy application-owned
transcripts, product commands, workspace registries, or durable orchestration
state merely because they are adjacent in the source repository.

## QQ, Telegram, Feishu, and Weixin transfer

Primary source:

```text
repository: https://github.com/albert-zen/imcodex
transferred commit: 858398226e8f76e49f8259ae686939f209e1bb36
```

Transferred source families:

```text
src/imcodex/channels/base.py
src/imcodex/channels/access.py
src/imcodex/channels/media.py
src/imcodex/channels/text.py
src/imcodex/channels/qq.py
src/imcodex/channels/qq_media.py
src/imcodex/channels/telegram.py
src/imcodex/channels/feishu.py
src/imcodex/channels/weixin*.py
src/imcodex/models.py
src/imcodex/file_types.py
src/imcodex/windows_security.py
```

Behaviors to preserve:

- stable account/conversation/sender identity;
- access and duplicate checks before attachment work;
- Markdown conversion and plain-text fallback;
- ordered segmentation and one sender per destination;
- native delivery IDs when a platform response actually provides them, stable
  SDK delivery IDs otherwise, and conservative retry;
- attachment staging and platform size limits;
- reconnect tokens that belong to the Channel adapter.

The QQ, Telegram, Feishu, and Weixin provider transports plus shared HTTP
endpoint validator now live under `src/imagent/interaction/channels/adapters/`.
Shared access, inbound staging, and Windows path security live with the
Interaction ingress owner, and shared generic-file validation lives with
the Interaction media owner. Shared defensive text splitting lives with
Interaction Channel outbound delivery. Product middleware, registry, commands,
login UX, configured allowlist values and UX, bot policy, and deployment
configuration were deliberately excluded.

Provider-private transport DTOs are split between the Interaction Channel
ingress and outbound-delivery owners; they are leaf-internal values, not common
Message or Operation contracts.

The SDK no longer imports the consumer package. Downstream adoption status is
tracked on [GitHub issue #9](https://github.com/albert-zen/im-agent-sdk/issues/9),
not in SDK product documentation.

### Exact Channel source map

| IMCodex source | SDK destination or decision |
|---|---|
| `channels/access.py` | `interaction/channels/ingress.py`; stable-ID access policy only |
| `channels/base.py` | `interaction/channels/adapters/base.py`; lifecycle/access base without product telemetry or a historical shim |
| `channels/artifacts.py` | `interaction/channels/outbound_delivery.py`; one native attachment attempt, while consumers retain bytes/root/quota/ledger/sweep ownership |
| `channels/media.py` | `interaction/channels/ingress_media.py`; bounded staging with its shared lock/quota/secure-create/cleanup/cancellation transaction boundary intact |
| `channels/text.py` | `interaction/channels/outbound_delivery.py`; defensive native text splitting |
| `channels/qq.py`, `channels/qq_media.py` | `interaction/channels/adapters/qq.py`, `qq_media.py` |
| `channels/telegram.py` | `interaction/channels/adapters/telegram.py` |
| `channels/feishu.py` | `interaction/channels/adapters/feishu.py`; optional SDK loading retained |
| `channels/weixin_ilink.py`, `channels/weixin_state.py`, `channels/weixin.py` | matching Interaction adapter modules; credential/reconnect state stays Channel-owned and enrollment UX stays downstream |
| top-level `models.py` | split into private ingress and outbound-delivery DTOs |
| top-level `file_types.py` | `interaction/media.py`; shared generic-file byte validation |
| top-level `windows_security.py` | `interaction/channels/ingress_security.py`; secure staging without a historical shim |
| top-level `config.py` | only neutral endpoint validation moved to `interaction/channels/adapters/endpoints.py`; product configuration did not transfer |

`channels/api.py`, `channels/middleware.py`, `channels/outbound.py`,
`channels/registry.py`, `channels/weixin_login.py`, and the product
`channels/__init__.py` were deliberately excluded because they own product
HTTP/composition, bridge workflow, configuration, registry, enrollment UX, or
exports rather than reusable Channel semantics.

## Codex App Server client transfer

Reusable target, retry, JSON-RPC client, and supervisor modules were
transferred from the same pinned IMCodex commit into
`src/imagent/applications/adapters/appserver/client/`; App Server diagnostic facts and
internal helpers are now owned by
`src/imagent/applications/adapters/appserver/diagnostics.py`,
protocol/resource mapping is owned by
`src/imagent/applications/adapters/appserver/mapping.py`, and framing/closure is owned by
`src/imagent/applications/adapters/appserver/transport.py`. The concrete
Codex and Zen Application owners are
`src/imagent/applications/adapters/codex.py` and
`src/imagent/applications/adapters/zen.py`; their private shared base is
explicitly mapped to both owners and exposes no aggregate facade. They support
spawned stdio, Unix-socket, and TCP WebSocket endpoints without importing
product backends or configuration. Consumer supervision, branding, commands,
and product policy were not promoted to Core.

Additional reviewed source, not copied by this transfer:

```text
repository: https://github.com/pingdotgg/t3code
reviewed local commit: 735821c50b3c2829d8ed0893381ee70ded6461f6
license: MIT, Copyright (c) 2026 T3 Tools Inc.
```

Potential future protocol/client package:

```text
packages/effect-codex-app-server/
```

Candidate application mapping:

```text
apps/server/src/provider/Layers/CodexAdapter.ts
apps/server/src/provider/Layers/CodexSessionRuntime.ts
apps/server/src/provider/Layers/CodexProvider.ts
apps/server/src/provider/Drivers/CodexDriver.ts
```

The package already contains generated schema, protocol/RPC handling, stdio
transport, mock peers, probes, and tests. Prefer importing or extracting it
with attribution over rebuilding Codex App Server framing and lifecycle.

The IM Agent SDK adapter translates Codex resources and events into common
contracts. It does not make Codex an execution backend inside Zen.

### Exact App Server source map

| IMCodex source | SDK destination or decision |
|---|---|
| `app_server_target.py` | `applications/adapters/appserver/client/target.py`; endpoint/ownership model with neutral configuration wording |
| `appserver/retry.py` | `applications/adapters/appserver/client/retry.py` |
| `appserver/protocol_map.py` | `applications/adapters/appserver/mapping.py` |
| `appserver/diagnostics.py` | `applications/adapters/appserver/diagnostics.py`; fixed bounded redacted facts/helpers |
| `appserver/client.py` | `applications/adapters/appserver/client/client.py`; JSON-RPC connection-epoch state machine kept coherent |
| `appserver/supervisor.py` | `applications/adapters/appserver/client/supervisor.py`; product telemetry removed |

`appserver/backend*.py`, `settings_backend.py`, `thread_backend.py`,
`thread_dynamic_tools.py`, and `schema_drift.py` were excluded as consumer
workflow, backend composition, or development tooling. The public factory is
`codex_app_server_client`; the IMCodex-named dynamic import was deleted.

The transferred source repository and commit contained no `LICENSE` file or
declared license. That historical absence is preserved as provenance. The SDK
code maintained here, including these transferred portions, is now provided
under the repository's [MIT License](../LICENSE). Local changes are limited to the package
namespace, removal of consumer observability/configuration/store/backend
dependencies, neutral caller-provided or `.imagent` state paths, standard
logging and explicit adapter errors, and translation only at Channel or
Application boundaries. Product commands and Agent state were not copied.

Transfer evidence was selected from these exact pinned-source tests:

- App Server: `test_appserver_stdio.py` and `test_appserver_target.py`;
- common Channel/access/media: `test_channel_foundations.py`,
  `test_channel_files.py`, and focused middleware admission cases; and
- native transports: `test_channels.py`, `test_channel_telegram.py`,
  `test_channel_feishu.py`, `test_channel_weixin.py`,
  `test_channel_weixin_ilink.py`, and `test_qq_media.py`.

Tests were adapted to SDK Ports and retained only where behavior belongs to
the transferred boundary. Product CLI, HTTP API, registry, webhook
composition, and IMCodex configuration tests were excluded. Base imports
remain independent of native extras, and provider/App Server extras are
verified through clean-install construction without the consumer package.

The historical source transfer does not create a runtime or release dependency
on IMCodex; these are separately maintained projects.

## Zen and T3 proof sources

Zen and the T3 Zen fork provide integration fixtures for:

- stable `clientUserMessageId` round-trip;
- externally originated canonical user-message projection;
- independent application identity;
- project/thread navigation;
- snapshot plus streaming reconciliation.

Their product-specific provider, model, runtime-mode, project UI, and command
semantics remain outside the shared SDK.

## Extraction method

1. Pin and record a source commit.
2. Copy or import code with its tests and license notice.
3. Establish behavior under the source repository's existing test suite.
4. Introduce the common adapter boundary around the copied behavior.
5. Remove unrelated product dependencies incrementally.
6. Add shared contract tests before changing semantics.
7. Document intentional divergence from the source.

## Installed dependency notices

The SDK wheel does not vendor its Python dependencies. Package managers install
these separately; their distributions carry their own notices and licenses.
The locked runtime dependency metadata currently declares:

| Dependency | Version in `uv.lock` | Declared license |
|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause |
| websockets | 15.0.1 | BSD-3-Clause |
| Pillow | 12.3.0 | MIT-CMU |
| lark-channel-sdk | 1.2.0 | MIT AND BSD-3-Clause |
| pycryptodome | 3.23.0 | BSD and Public Domain |

This table records upstream package metadata, not a replacement for each
package's full license files or a license grant over the migrated source above.
When redistributing a combined environment, preserve the dependencies' notices
and review their bundled third-party notices as well as their top-level license.
The repository's development launcher obtains AgentKit separately at its pinned
Git revision; AgentKit is MIT-licensed and is not included in the SDK wheel.

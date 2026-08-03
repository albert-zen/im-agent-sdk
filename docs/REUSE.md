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

Provider transports remain under `src/imagent/channels/native/`; shared access
policy lives with the Interaction ingress owner, and shared generic-file
validation lives with the Interaction media owner. Exact source-to-destination
decisions, exclusions, local modifications, and test proof are recorded in the
[Issue #9 transfer map](migrations/issue-9-imcodex-owner-transfer.md). Product middleware,
registry, commands, login UX, configured allowlist values and UX, bot policy,
and deployment configuration were deliberately excluded.

The SDK no longer imports the consumer package. The later IMCodex migration
must consume these SDK APIs and delete its duplicated production copies before
Issue #9 can close.

## Codex App Server client transfer

Reusable target, retry, protocol-map, diagnostics, JSON-RPC client, and
supervisor modules were transferred from the same pinned IMCodex commit into
`src/imagent/applications/appserver_client/`. They support spawned stdio,
Unix-socket, and TCP WebSocket endpoints without importing product backends or
configuration. Consumer supervision, branding, commands, and product policy
were not promoted to Core.

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

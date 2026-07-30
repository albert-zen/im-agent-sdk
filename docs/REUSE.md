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

## Channel adapter source

Primary source:

```text
repository: https://github.com/albert-zen/imcodex
reviewed commit: 9f2f38da44aa88af0cdb82917cfee1d93ce02675
```

Candidate paths:

```text
src/imcodex/channels/base.py
src/imcodex/channels/access.py
src/imcodex/channels/middleware.py
src/imcodex/channels/outbound.py
src/imcodex/channels/media.py
src/imcodex/channels/text.py
src/imcodex/channels/qq.py
src/imcodex/channels/qq_media.py
src/imcodex/channels/telegram.py
src/imcodex/channels/feishu.py
src/imcodex/channels/weixin*.py
```

Behaviors to preserve:

- stable account/conversation/sender identity;
- access and duplicate checks before attachment work;
- Markdown conversion and plain-text fallback;
- ordered segmentation and one sender per destination;
- native delivery IDs and conservative retry;
- attachment staging and platform size limits;
- reconnect tokens that belong to the Channel adapter.

IMCodex is owner-controlled code but currently has no repository license file.
Do not publish copied source outside owner-controlled repositories until its
licensing is made explicit.

## Codex App Server source

Primary source:

```text
repository: https://github.com/pingdotgg/t3code
reviewed local commit: 735821c50b3c2829d8ed0893381ee70ded6461f6
license: MIT, Copyright (c) 2026 T3 Tools Inc.
```

Candidate protocol/client package:

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

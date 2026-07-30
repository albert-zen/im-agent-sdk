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
runtime dependency commit: 858398226e8f76e49f8259ae686939f209e1bb36
newer local implementation reviewed: 9f2f38da44aa88af0cdb82917cfee1d93ce02675
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

The current implementation imports the pinned owner-controlled package instead
of copying its source. This retains its channel lifecycle, access policy,
attachment, Markdown, reply, and retry behavior at the tested boundary.

Issue #9 removes this reverse dependency. Before implementation it must publish
a transfer map covering each source module, tests/fixtures, license notice,
configuration owner, local modification, and deletion point. After transfer,
the SDK owns reusable adapter/client/test code and IMCodex is a downstream
consumer. Product commands, allowlists, bot policy, Full Access, and deployment
configuration remain explicit consumer decisions.

## Codex App Server source

The current Python implementation imports the pinned IMCodex App Server client
and supervisor. It supports spawned stdio and remote WebSocket endpoints.
Issue #9 transfers only reusable client/protocol/lifecycle behavior into the
SDK; IMCodex-specific supervision or product configuration is not promoted to
Core.

Additional reviewed source:

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

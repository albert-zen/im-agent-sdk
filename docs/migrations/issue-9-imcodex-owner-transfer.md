# Issue 9 SDK ownership transfer map

Status: SDK owner-side implementation; downstream consumer migration pending

The first downstream cutover review found additional adapter/runtime blockers.
Their classifications and merge-before-cutover rule are recorded in
[IMCodex consumer follow-up blockers](imcodex-followup-blockers.md).

This map separates the work that can be completed in `im-agent-sdk` from the
later IMCodex consumer migration. It is not a claim that Issue #9 is complete.

## Acceptance split

| Scope | This SDK PR | Later isolated IMCodex work |
|---|---|---|
| Package direction | remove the `imcodex` dependency/extra from package metadata, lockfile, CI, runtime imports, and tests | add the SDK dependency and remove the old product-to-SDK reverse edge |
| Runtime ownership | own reusable QQ, Telegram, Feishu, Weixin transports and the Codex App Server client/supervisor | compose SDK adapters from product configuration |
| Source deletion | delete SDK dynamic-import seams and their IMCodex-named public API | delete the duplicated IMCodex implementations after downstream parity |
| Configuration/policy | expose adapter-native constructor/config values only | retain config-file/env loading, commands, allowlists, branding, launchers, Full Access, and deployment choices |
| Issue state | `Refs #9`; prove the owner-side criteria | finish consumer parity/removal before Issue #9 can close |

## Provenance and license fact

This section is the repository's authoritative source-provenance record for
the copied implementation. The transferred destination families are
`src/imagent/interaction/channels/` and
`src/imagent/applications/appserver_client/`; the detailed tables below record
the source paths, exclusions, modifications, and test proof. This provenance
record is not a replacement for a repository license.

The transfer source is
[`albert-zen/imcodex`](https://github.com/albert-zen/imcodex) commit
`858398226e8f76e49f8259ae686939f209e1bb36`, which is the commit already pinned
by this repository. The source commit and its package metadata contain no
`LICENSE` file or declared license. This transfer therefore records provenance
without inventing a license label. The owner-directed transfer does not by
itself state third-party redistribution terms; a repository license remains a
maintainer/legal decision.

Local modifications must be reviewable:

- change the package namespace from `imcodex` to `imagent`;
- remove IMCodex observability/config/store/backend dependencies;
- retain standard logging and explicit adapter errors, leaving telemetry
  export to Issue #13;
- replace `.imcodex` default state paths with neutral caller-provided or
  `.imagent` paths;
- translate only at the Channel/Application adapter boundary, never by
  copying product commands or Agent state.

Two large transferred modules intentionally retain their proven transaction
boundaries in this owner-side move. `interaction/channels/ingress_media.py`
keeps image and generic
file staging together because both use the same cross-process lock, quota,
secure-create, cleanup, and cancellation machinery.
`appserver_client/client.py` keeps one connection-epoch JSON-RPC state machine;
splitting its request, notification, reconnect, and pending-future state during
the ownership transfer would change failure behavior. AgentKit budgets are set
just above these baselines so future growth forces a fresh extraction review;
this is not permission to add consumer policy to either module.

The SDK-owned client also preserves the native input side-effect boundary:
after `turn/start` dispatch begins, cancellation, timeout, disconnect, or
response loss is an unknown outcome rather than permission to send the same
input again. Gateway persists that distinction independently of IMCodex.

## Channel transport modules

All four transferred provider transports now live in
`src/imagent/interaction/channels/adapters/`; shared ingress and delivery
helpers live under their Interaction Channel owners without a historical
native helper package.
Their shared access policy now lives with the Interaction ingress owner at
`src/imagent/interaction/channels/ingress.py`, and shared generic-file
validation lives at `src/imagent/interaction/media.py`. Provider-native models
and helpers remain private implementation, not a second copy of SDK Core
contracts.

| IMCodex source | SDK destination/decision | Ownership and modification |
|---|---|---|
| `channels/access.py` | `interaction/channels/ingress.py` | transfer shared stable-ID access policy to Interaction ingress; consumers choose configured IDs |
| `channels/base.py` | `interaction/channels/adapters/base.py` | transfer the single lifecycle/access base to its Interaction adapter owner; preserve behavior, replace product telemetry with logging, and retain no historical native-path shim |
| `channels/artifacts.py` | `interaction/channels/outbound_delivery.py` | transfer shared attachment-send helpers for one native attempt; consumer retains bytes/root/quota/ledger/sweep ownership |
| `channels/media.py` | `interaction/channels/ingress_media.py` | transfer bounded staging/materialization to Interaction ingress; keep the shared transaction boundary and Channel-local trust |
| `channels/text.py` | `interaction/channels/outbound_delivery.py` | transfer shared defensive native text splitting to Interaction outbound delivery |
| `channels/qq_media.py` | `interaction/channels/adapters/qq_media.py` | transfer QQ media upload/download behavior |
| `channels/qq.py` | `interaction/channels/adapters/qq.py` | transfer QQ transport; replace product config/path imports |
| `channels/telegram.py` | `interaction/channels/adapters/telegram.py` | transfer Telegram transport; replace product config/path imports |
| `channels/feishu.py` | `interaction/channels/adapters/feishu.py` | transfer Feishu transport; preserve optional native SDK loading |
| `channels/weixin_ilink.py` | `interaction/channels/adapters/weixin_ilink.py` | transfer iLink protocol/crypto transport |
| `channels/weixin_state.py` | `interaction/channels/adapters/weixin_state.py` | transfer Channel-owned credential/reconnect state |
| `channels/weixin.py` | `interaction/channels/adapters/weixin.py` | transfer Weixin transport; product login UX remains downstream |
| top-level `models.py` | `interaction/channels/ingress.py` and `interaction/channels/outbound_delivery.py` | split provider-private inbound and outbound/native-delivery DTOs by Interaction owner |
| top-level `file_types.py` | `interaction/media.py` | transfer shared generic-file type/byte validation to Interaction media |
| top-level `windows_security.py` | `interaction/channels/ingress_security.py` | transfer the private secure-staging helper to Interaction ingress without a historical native-path shim |
| top-level `config.py` | no wholesale transfer | neutral endpoint validation now lives in `interaction/channels/adapters/endpoints.py`; product config loading remains IMCodex |

The following adjacent modules do not transfer:

| IMCodex source | Reason |
|---|---|
| `channels/api.py` | product HTTP ingress/composition |
| `channels/middleware.py` | IMCodex bridge workflow; the SDK boundary has its own minimal normalization/admission middleware |
| `channels/outbound.py` | product webhook/multiplex composition; Gateway delivery work belongs to Issues #11/#12 |
| `channels/registry.py` | product config registry/enablement |
| `channels/weixin_login.py` | consumer credential-enrollment UX |
| `channels/__init__.py` | product export surface |

The public SDK surface replaces `ImcodexChannelAdapter`/`imcodex_channel` with
SDK-owned per-platform adapters/factories. It does not keep an exitless alias.
Protocol-specific dependencies remain optional extras in one SDK
distribution.

## Codex App Server client modules

Reusable protocol/client code lives below
`src/imagent/applications/appserver_client/`.

| IMCodex source | SDK destination/decision | Ownership and modification |
|---|---|---|
| `app_server_target.py` | `target.py` | transfer endpoint/ownership model; remove IMCodex env-name wording |
| `appserver/retry.py` | `retry.py` | transfer transport retry primitive |
| `appserver/protocol_map.py` | `adapters/appserver/mapping.py` | transfer protocol notification/request classification |
| `appserver/diagnostics.py` | `adapters/appserver/diagnostics.py` | transfer App Server diagnostic facts and internal debug helpers |
| `appserver/client.py` | `client.py` | transfer JSON-RPC, stdio/WebSocket, queue, reconnect, and request dispatch |
| `appserver/supervisor.py` | `supervisor.py` | transfer spawned-stdio/external endpoint lifecycle; remove product telemetry |

`appserver/backend*.py`, `settings_backend.py`, `thread_backend.py`,
`thread_dynamic_tools.py`, and `schema_drift.py` do not transfer in this PR.
They contain IMCodex conversation/product behavior, backend composition, or
development tooling outside the client required by the SDK Application
adapter.

The public factory becomes `codex_app_server_client`. The
`imcodex_app_server_client` dynamic-import name is deleted. Codex/Zen
Application adapters continue to translate native resources and events; the
client never becomes Agent truth.

## Packaging

One distribution provides protocol extras rather than separate packages:

- `appserver`: WebSocket support for remote Codex App Server endpoints;
- `qq`, `telegram`, `feishu`, and `weixin`: each protocol's optional native
  dependencies;
- `channels`: the union for deployments using all four.

Importing base Contracts/Ports/Gateway must not require any protocol extra.
Installing the relevant extra allows each adapter to import and construct in a
clean environment with no `imcodex` package. Network connection and real
credential validation remain deployment smoke tests, not installation side
effects.

## Test transfer and proof

Focused behavior is selected from the pinned source tests:

- App Server: `test_appserver_stdio.py` and `test_appserver_target.py`;
- common Channel/access/media: `test_channel_foundations.py`,
  `test_channel_files.py`, and focused middleware admission cases;
- native transports: `test_channels.py`, `test_channel_telegram.py`,
  `test_channel_feishu.py`, `test_channel_weixin.py`,
  `test_channel_weixin_ilink.py`, and `test_qq_media.py`.

Tests are adapted to SDK Ports and kept only when their behavior belongs to
the transferred boundary. Product CLI, HTTP API, registry, webhook
composition, and IMCodex config tests do not transfer.

Owner-side completion requires:

1. no case-insensitive `imcodex` reference in `pyproject.toml`, `uv.lock`,
   `.github/workflows`, or `src/imagent`;
2. default and relevant-extra clean-install import/construction smoke tests;
3. focused QQ/Telegram/Feishu/Weixin and App Server behavior tests against
   SDK-owned code;
4. reusable Channel/Application contract tests and the full SDK suite;
5. provenance/source-path records and no product configuration or command
   code in the transferred graph.

The later IMCodex branch must prove downstream parity, switch composition to
these SDK APIs, and delete its duplicate production implementations before
Issue #9 can be closed.

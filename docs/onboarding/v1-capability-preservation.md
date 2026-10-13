# v1 capability-preservation checkpoint

This is a migration ledger, not a compatibility layer or a claim of full product
acceptance. The audit compares old `7d5f1179365679d0f95abd5cb2ce76547238d05f`
and Zen steering `bb80550` against v1 base
`c5d6ab627aef3fee10b1e3a3b44ff650bdefa0b5` (2026-10-13).

The source audit enumerated all 87 old modules and 502 non-private top-level
definitions. Of these, 494 names still had definitions on v1 main. Same-name
presence is only navigation evidence: it does not establish public API or
behavioral equivalence. Eight removed names are classified below. The detailed
owner matrix covers commands, input, requests, media, proactive delivery,
routing, persistence, recovery, transport, diagnostics, and release.

The identified gaps restored in this change are Zen native steering and typed
proven input rejection. QQ retains the v1 main Markdown behavior by the
consumer owner's explicit 2026-10-13 decision; the old per-message plain-text
override is not reinstated. Zen image
support is restored by safe rooted byte acquisition and data-URI submission;
Codex's unsafe path-only route remains rejected. The optional real-Zen test
uses the public SDK Gateway, SQLite store, adapter, and production App Server
client against a pinned Zen source checkout. Only its model and IM transport
are synthetic. It proves same-Turn text/image input, immutable reply destination,
foreign/stale/ended-Turn fences, busy rejection, fresh v1 SQLite restart, and
post-restart input. This is not evidence of upgrading an old deployment database.

Paths below are relative to `src/imagent/`; test paths are repository-relative.

## Capability matrix

Statuses: **M** = owner/implementation and test evidence statically located, behavior not run by this audit; **G** = genuine main behavior gap, restored here; final evidence in owner suites; **S** = intentional safety/authority change, do not mechanically restore old behavior; **C** = product-consumer acceptance required; **P** = independently probed here.

| Capability | Old owner/evidence | Main/candidate owner and behavior | Verification/status |
|---|---|---|---|
| Public application/channel contracts | `adapters.py`, `contracts/model.py`, root module exports | `applications/contract.py`, `interaction/messages.py`, `interaction/channels/contract.py`; application/channel surfaces are still typed | M: `tests/applications/test_contract.py`, `tests/interaction/channels/test_contract.py`; import paths changed |
| Public Gateway composition | `gateway.py:ImAgentGateway`, `gateway_composition.py:GatewayRepositories` | `gateway/runtime.py:Gateway`, `gateway/composition.py`, one `GatewayStore` | M/S: `tests/gateway/test_package_root.py`, `test_reference_consumer.py`; old repository-bundle API deliberately retired |
| Application → project/workspace → thread → turn identity | old optional-project references in `contracts/model.py` | `applications/contract.py` mandatory nested ancestry, `WorkspaceIdentity`, fingerprint; stable workspace ID, not path-as-identity | M/S/C: `tests/applications/test_contract.py`, `test_capabilities.py`; downstream configuration/ref construction must migrate |
| Project/thread discovery, query, snapshot/status | old `contracts/operations.py`, application adapters | `applications/operations.py`; `gateway/actions.py:ApplicationActions/ConversationActions` read methods | M: `tests/gateway/test_actions.py`, application adapter suites |
| Thread creation and binding | old slash handler hand-composed native create + bind | `ConversationActions.create_and_bind_thread`, effect execution/store workflow | M: `tests/gateway/test_actions.py`, `test_effect_execution.py`; workflow is the new public owner |
| Project creation/selection | old operations and consumer composition | `create_and_select_project`; unsupported management remains explicit on fixed/flat adapters | M: `tests/gateway/test_actions.py`, `tests/applications/test_operations.py` |
| Native thread activation | old `ActivateNativeThread`, Gateway/application operations | `activate_native_thread`, distinct from binding and observation | M: `tests/gateway/test_actions.py`; no automatic activation inferred from a bind |
| Delete/archive thread | old slash and application operation; declared deletion mode | `delete_thread`, `ThreadDeletionMode`, common `/delete` and `/archive` | M/S: `tests/interaction/controllers/test_common_commands.py`, `tests/gateway/test_actions.py`; primitive delete deliberately does not silently clear binding |
| Interrupt/cancel native turn | old `InterruptTurn` application operation | same operation at `applications/operations.py`, scoped `interrupt_turn` at `gateway/actions.py` | M: `tests/applications/test_operations.py`, `tests/gateway/test_actions.py`; unsupported adapter cases must remain explicit |
| Common command grammar | `controllers/slash.py:SlashController` | `interaction/controllers/registry.py:CommandRegistry` plus `common.py:include_common_commands` and scoped actions | M/C: `test_common_commands.py`, `test_registry.py`; app/apps, projects/use/project, threads/pick/thread, new, delete/archive, status, catchup, history, respond/answer, help/start have dispatch branches |
| Custom controller/product commands | `controllers/base.py:ControllerActions`, `SlashController.handle` | immutable `ConversationActions`, `CommandDefinition`, handler time/input bounds, explicit registry composition/freeze | M/C: `tests/interaction/controllers/test_optional_controller.py`, `test_registry.py`; consumer registration/config migration not proven |
| Implicit single-app selection/automatic thread onboarding | old `gateway.py:_single_application_or_none`, `_process_message` | canonical ordinary input requires complete live binding; optional consumer Controller performs explicit onboarding | S/C: `gateway/input/dispatch.py:MissingBindingError`, `StaleBindingError`; `tests/gateway/input/test_policy_free_ordinary_input.py`; losing old automatic UX is acceptable only if intended product onboarding is implemented downstream |
| Ordinary input authority/preflight | old Gateway/projection input path | `gateway/input/dispatch.py`, `gateway/orchestration.py`; authoritative project/thread reads before transform/dispatch | M: `test_policy_free_ordinary_input.py`, `test_dispatch.py` |
| Codex prefer-active-turn steering | old `applications/appserver.py:CodexApplicationAdapter.send_input` | `applications/adapters/codex.py` owns Codex discovery/steer policy | M: `tests/applications/adapters/test_codex.py`, `appserver/test_input_integration.py` |
| Zen prefer-active-turn steering | `bb80550`, old shared App Server/Zen adapter | main `adapters/zen.py` was constructor only, inheriting start behavior; Zen now owns independent Zen send/steer logic | G: candidate `tests/applications/adapters/test_zen.py` includes active discovery, fencing, no-fallback tests; covered by the final owner suites and native interop |
| Start-new opt-out and reply correlation | old `InputContinuationPreference`, `ApplicationInputDispatch`, `TurnReplyCorrelationPolicy` | `applications/contract.py`; `gateway/input/dispatch.py`; candidate Zen uses preserved existing-turn correlation for steering | M/G: `test_input_integration.py`, `tests/gateway/input/test_dispatch.py`; real-Zen interop additionally covers steering and original reply preservation |
| Proven negative native input rejection | `7d5f117`, `contracts/errors.py:ApplicationInputRejected`, App Server client allowlist | absent from exact main; candidate `applications/contract.py` + client method/code allowlist + failure presenter | G: `test_client.py`, `test_failure_presentation.py`; require turn/steer+turn_not_running and turn/start+thread_busy only, terminal claim, no automatic resend |
| Ambiguous dispatch and transport errors | old `ApplicationInputOutcomeUnknown`, client dispatch fence | same typed contract in `applications/contract.py`, client and Gateway failure owner | M: client/input/failure suites; unknown outcome must remain fenced rather than be reclassified by arbitrary exception strings |
| I1 inbound content transformation | `inbound_content.py` | `gateway/input/content_transformation.py`; content-only seam after verified envelope and binding | M: `tests/gateway/input/test_content_transformation.py` |
| I2 bounded failure presentation | `inbound_failures.py` | `gateway/input/failure_presentation.py` bounded phase/facts; this change adds explicit rejected phases | M/G: `test_failure_presentation.py`; no native exception payload or dispatch authority exposed |
| Native approval/user-input requests | `applications/appserver_requests.py`, `appserver_request_runtime.py`, controller requests | `applications/requests.py`, `adapters/appserver/requests.py`, `interaction/controllers/request_presentation.py` | M: `tests/applications/adapters/appserver/test_requests.py`, `test_gateway_request_integration.py`, controller request presentation suite |
| Respond authorization, stale/duplicate requests | old `request_projection_runtime.py`, `request_correlations.py` | `gateway/projection/request_correlation.py`, `ConversationActions.respond_request`, coherent durable receipts | M: `tests/gateway/projection/test_request_correlation.py`, `tests/gateway/test_reference_consumer.py:test_public_restart_marks_request_stale_without_authoritative_snapshot` |
| Request correlation cleanup | old `InteractiveRequestProjection.cleanup_older_than` | new `InteractiveRequestProjection.cleanup_correlations` and repository `delete_request_correlations` | M: method-name removal is ownership/API change, not evidence of cleanup loss |
| Text and typed content/media contracts | `contracts/model.py`, `attachments.py` | `interaction/messages.py`, `interaction/media.py`, `media_staging.py` | M: `tests/interaction/test_messages.py`, `test_media.py`, `test_media_staging.py` |
| Rooted local attachment acquisition | old local-path validation | no-follow/rooted acquisition in `interaction/media.py`; Channel outbound paths use byte-bound managed artifact read | S/M: media and native channel tests; root escape/symlink/replacement must remain rejected |
| Zen inbound images | old App Server localImage path behavior | main safely rejects path-only input; candidate Zen converts acquired bytes to bounded data URI image payload | S/G: candidate `test_zen.py` rooted start/steer image tests; real-Zen interop and owner tests cover byte transfer, trust negatives, and aggregate caps |
| Codex inbound images | old App Server localImage path behavior | exact main and intended candidate remain unsupported for path-only native open | S/C: do not advertise restored Zen capability as Codex parity; if product requires Codex images, it is a separate safe implementation gap |
| T3 media/input | `applications/t3.py`, `t3_client.py` | `applications/adapters/t3.py` including `HttpT3Client`; rooted byte path | M: `tests/applications/adapters/test_t3.py`, Gateway vertical-slice byte acquisition cases |
| Artifact discovery/materialization | `applications/appserver_artifacts.py` | `applications/presentation/artifact_materialization.py`, adapter live/history integration | M/C: `test_artifact_materialization.py`; consumer owns bytes, spool/trust root, ledger, quotas/cleanup; typed seam alone does not prove product materializer installed |
| Codex live/T3 recoverable activity presentation | `applications/presentation.py` | `applications/presentation/live_activity.py`; live-only Codex vs history-reproducible T3 distinction retained | M/C: `tests/applications/presentation/test_live_activity.py`; consumer presenter integration remains separate |
| Destination-specific outbound presentation | `outbound_presentation.py` | `gateway/presentation.py` | M: `tests/gateway/test_presentation.py` |
| Logical delivery planning and bounded ordering | `delivery_planning.py`, `delivery_coordination.py` | `gateway/delivery/planning.py`, `coordination.py` | M: `tests/gateway/delivery/test_planning.py`, `test_coordination.py` |
| Delivery receipts, partial/artifact outcomes | `delivery_outcomes.py`, native artifact helpers | `gateway/delivery/outcome_observation.py`, `interaction/channels/outbound_delivery.py` | M: `test_outcome_observation.py`, `test_outbound_delivery.py`; optional callback/materializer must be wired by consumer |
| QQ explicit text vs global Markdown | `7d5f117` added a per-message plain-text override | v1 main native Markdown behavior is retained by explicit consumer-owner decision on 2026-10-13 | S/C: ordinary text remains valid Markdown; verbatim blocks are a presentation choice, not a global encoding override |
| QQ quote/attachments/direct/group behavior | `channels/native/qq.py`, `qq_quote.py`, `qq_media.py` | `interaction/channels/adapters/qq*.py` and typed ingress/outbound owners | M: `tests/interaction/channels/adapters/test_qq.py`, `test_qq_facade.py`; supported-group-file restrictions are explicit, not missing generic-file capability |
| Telegram channel | `channels/native/telegram.py` | `interaction/channels/adapters/telegram.py` | M: `tests/interaction/channels/adapters/test_telegram.py`; real transport not exercised |
| Feishu channel | `channels/native/feishu.py` | `interaction/channels/adapters/feishu.py` | M: `tests/interaction/channels/adapters/test_feishu.py`; real transport not exercised |
| Weixin/iLink state/reconnect | `channels/native/weixin*.py` | `interaction/channels/adapters/weixin*.py` | M: `tests/interaction/channels/adapters/test_weixin.py`; credentials/native transport were not used |
| Channel factory/start/access/media admission | `channels/runtime.py`, native base/access/media | `interaction/channels/adapters/runtime.py`, `base.py`, `ingress.py`, `ingress_media.py`, `ingress_security.py` | M/S: corresponding native-channel/owner tests; removed permissive fallback must not be restored to bypass declared lifecycle |
| Inbound durable admission/idempotency | `inbound_admission.py`, `storage.py`, old repositories | `gateway/admission.py`, coherent leased persistence session/idempotency | M: `tests/gateway/test_admission.py`, persistence idempotency and store/fencing suites |
| Binding generation and concurrent mutations | `bindings.py`, old binding repository | `gateway/routing/bindings.py`, `actions.py`, store/effect transaction | M/S: binding/action/store tests; atomic generation+receipt path replaces loose repository composition |
| Projection policies and route lifecycle | `projection_routes.py:ProjectionRouteCoordinator` | private `_ProjectionRouteAuthority` at `gateway/routing/projection_routes.py`, scoped observe/clear actions | M: `test_projection_routes.py`, `test_projection_integration.py`; foreground_only, remembered_last_recipient, all_observers remain enum values |
| Subscribe-before-send and turn acceptance gate | `projection_runtime.py:ThreadProjectionRuntime.send_input` | `gateway/input/dispatch.py` owns dispatch; observation stays `gateway/projection/observation.py` | M: `test_dispatch.py`, `test_observation.py`; moved method is not dropped input support |
| Live events/fanout/checkpoints/history recovery | `events.py`, `projections.py`, `recovery.py`, projection runtime | `applications/events.py`, `gateway/projection/checkpoints.py`, `recovery.py`, `observation.py` | M: `test_events.py`, `test_checkpoints.py`, `test_recovery.py`, `test_hardening.py`; bounded loss invokes recovery/degraded truth |
| Proactive send, route snapshots, authorization | `proactive_delivery.py`, `delivery_submissions.py`, Gateway | `gateway/delivery/proactive*.py`, `submissions.py`; public Gateway delegates delivery/authorization | M: `test_proactive_delivery.py`, `test_proactive_authorization.py`, `test_proactive_runtime.py`; no arbitrary recipient authority introduced |
| Local proactive ingress and CLI | `delivery_ingress.py`, `cli/send.py`, imagent-send | `gateway/delivery/proactive_ingress.py`, `interaction/client_tools/send.py`; loopback credential/target validation | M/C: `test_proactive_ingress.py`, `test_send.py`; deployed endpoint/credentials and new required project target need consumer migration |
| SQLite/in-memory persistence | `storage.py`, SQLite request/submission mixins | `gateway/persistence/sqlite.py`, `sqlite_store.py`, `memory.py`, `memory_store.py`; flattened mixin behavior still present | M/S: persistence suites; one store namespace/lease/durability domain replaces public loose repos |
| Legacy row compatibility and restart | old project-optional stored rows | new `row_mapping.py` requires complete ancestry, workspace fingerprint and fencing; legacy unknown ancestry fails closed | S/C: `test_row_mapping.py`, store fencing tests; an existing deployment's migration/reset plan is not established by this audit |
| Fresh-object restart/recovery and workflow replay | old startup/recovery/SQLite bridge state | `gateway/runtime.py`, `orchestration.py`, SQLite store, projection recovery | M: `test_reference_consumer.py` explicitly reconstructs fresh objects and tests missed output/no duplicate native calls; not rerun here |
| Cancellation, shutdown, lease loss | old gateway/client lifecycle and catches | `gateway/lifecycle.py`, runtime, leased effect execution, App Server transport supervisor | M: `test_lifecycle.py`, `test_reference_consumer.py` cancellation-after-bind/start rollback/lease-loss cases, `test_transport_lifecycle.py` |
| App Server transport, retries/reconnect/handoff | `applications/appserver_client/*` | `applications/adapters/appserver/client/*`, `transport.py`, `diagnostics.py` | M: `test_client.py`, `test_transport.py`, `test_transport_lifecycle.py`; do not infer native side-effect replay safety from reconnect support |
| Redacted bounded diagnostics | `diagnostics.py`, application/channel runtime diagnostics | `interaction/diagnostics.py`, `applications/diagnostics.py`, adapter diagnostics, `gateway/diagnostics.py`; public `Gateway.diagnostics()` | M: corresponding diagnostics suites; no user health payload/log sink was accessed |
| Adapter conformance and clean release consumer | `testing/contracts.py`, `testing/fakes.py`, old adapter tests | `interaction/testing/*`, `testing` facade, conformance tests, installed reference consumer | M: `tests/conformance/test_adapter_contracts.py`, `tests/engineering/test_release.py`, `tests/gateway/test_reference_consumer.py`; package/wheel smoke pending final candidate |

## The eight unmatched old definition names

- `ApplicationInputRejected`: true missing main contract and behavior, restored at its v1 owner.
- `ImAgentGateway`: public `Gateway` plus private `_GatewayRuntime`; public actions preserve supported controls under scoped APIs.
- `GatewayRepositories`: replaced by public coherent `GatewayStore`, `MemoryGatewayStore`, `SQLiteGatewayStore`. Do not add a compatibility layer accepting mixed durability domains.
- `ControllerActions`: replaced by scoped immutable `ConversationActions` / `ApplicationActions`.
- `SlashController`: replaced by `CommandRegistry` + `include_common_commands`; common command implementation remains in `interaction/controllers/common.py`.
- `ProjectionRouteCoordinator`: replaced by private `_ProjectionRouteAuthority` and public scoped route actions.
- `SQLiteRequestCorrelationMixin`: methods integrated into `gateway/persistence/sqlite.py:SQLiteGatewayState` (`list/put/transition/delete_request_correlations`).
- `SQLiteDeliverySubmissionMixin`: methods integrated into the same SQLite owner (`get/reserve/put` submission methods and store surface); not missing persisted delivery.

The root module's old exported module aliases (`adapters`, `contracts`, `delivery_coordination`, `delivery_planning`, `diagnostics`, `events`, `projections`, `recovery`) are intentionally no longer the finite v1 root facade. The new roots expose Gateway, scoped actions, outcomes, stores, and command registry. Product code must use supported new owners; absence of an old import path does not prove lost behavior, and an existing internal class does not by itself prove a supported public path.

## Reverse audit: significant v1 additions

The reverse audit found 152 main non-private top-level definition names absent from old. Material additions include `ApplicationActions`, `ConversationActions`, four-way action outcomes, explicit command safety/bounds and registry freeze, coherent memory/SQLite stores, workspace fingerprints, namespace leases and fencing, effect receipts/reconciliation, scoped workflows, binding-generation protections, and public lifecycle composition. These are relevant to preservation: restoring old behavior must use these new owners rather than route around them through old Gateway/repository facades.

## Outstanding consumer acceptance

- Rewrite IMCodex, IMT3, and IMZen against one exact SDK candidate and use only
  the supported v1 owners. Preserve product commands, onboarding, configuration,
  authorization, presentations, materializers, and proactive-tool composition.
- Validate the existing consumer's old SQLite database through a disposable
  copy and an explicit migration plan. Missing legacy ancestry is never guessed
  and bindings must never silently retarget; a fresh v1 restart is insufficient.
- Codex image input requires a separate evidenced byte-safe implementation.
  It must not be advertised as restored by Zen support.
- Run real product acceptance of session selection, cancellation, request loops,
  output visibility, and restart. SDK suites and symbol counts cannot replace it.

See [the v1 executable specification](../V1_EXECUTABLE_SPEC.md) and
[Zen adapter testing](../components/applications/adapters/zen/testing.md).

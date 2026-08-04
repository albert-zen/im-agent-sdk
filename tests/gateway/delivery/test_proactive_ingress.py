from __future__ import annotations

import asyncio
import base64
import inspect
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from imagent.applications.capabilities import ProjectMode
from imagent.applications.contract import ThreadRef
from imagent.cli.send import main as send_main
from imagent.contracts import DeliveryPrincipal
from imagent.gateway import GatewayLimits, GatewayRepositories, ImAgentGateway
from imagent.gateway.delivery import (
    DeliveryIntent,
    DeliverySubmissionOrigin,
    DeliveryTarget,
    ProactiveDeliveryJsonHandler,
    ProactiveDeliveryResult,
    ScopedDeliveryAuthorizer,
)
from imagent.gateway.delivery.proactive_ingress import (
    ProactiveDeliveryJsonHandler as OwnerProactiveDeliveryJsonHandler,
)
from imagent.gateway.delivery.submissions import derive_delivery_submission_id
from imagent.gateway.persistence import (
    DeliverySubmissionState,
    ThreadProjectionRoute,
)
from imagent.gateway.persistence.memory import (
    InMemoryBindingRepository,
    InMemoryDeliverySubmissionRepository,
    InMemoryProjectionRouteRepository,
)
from imagent.gateway.routing import ProjectionPolicy
from imagent.interaction.channels import (
    ChannelCapabilities,
    DeliveryReceipt,
    DeliverySupportLevel,
)
from imagent.interaction.media import (
    AttachmentContent,
    AttachmentGrouping,
    AttachmentSourceKind,
    LocalPath,
)
from imagent.interaction.messages import (
    ConversationRef,
    TextContent,
)
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _ReadingChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("channel")
        self._capabilities = ChannelCapabilities(
            markdown=DeliverySupportLevel.FALLBACK,
            attachments=DeliverySupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            attachment_grouping=AttachmentGrouping.MIXED,
            max_attachment_count=4,
            max_attachment_size=1024,
        )
        self.artifact_bytes: list[bytes] = []

    async def send(self, message) -> DeliveryReceipt:
        attachments = [item for item in message.content if isinstance(item, AttachmentContent)]
        for item in attachments:
            self.assert_local_path(item.source)
            assert isinstance(item.source, LocalPath)
            self.artifact_bytes.append(Path(item.source.path).read_bytes())
        return await super().send(message)

    @staticmethod
    def assert_local_path(source) -> None:
        if not isinstance(source, LocalPath):
            raise AssertionError("ingress must stage inline artifacts as LocalPath")


class _BlockingReadingChannel(_ReadingChannel):
    def __init__(self) -> None:
        super().__init__()
        self.send_attempts = 0
        self.started_read = asyncio.Event()
        self.cancelled_read = asyncio.Event()
        self.staged_path: Path | None = None
        self.path_existed_when_cancelled = False

    async def send(self, message) -> DeliveryReceipt:
        self.send_attempts += 1
        if self.send_attempts > 1:
            return await super().send(message)
        attachment = next(item for item in message.content if isinstance(item, AttachmentContent))
        assert isinstance(attachment.source, LocalPath)
        self.staged_path = Path(attachment.source.path)
        self.started_read.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.path_existed_when_cancelled = self.staged_path.exists()
            self.cancelled_read.set()
            raise
        raise AssertionError("blocking test Channel was unexpectedly released")


class _GatedReadingChannel(_ReadingChannel):
    def __init__(self) -> None:
        super().__init__()
        self.send_attempts = 0
        self.first_send_started = asyncio.Event()
        self.release_first_send = asyncio.Event()

    async def send(self, message) -> DeliveryReceipt:
        self.send_attempts += 1
        if self.send_attempts == 1:
            self.first_send_started.set()
            await self.release_first_send.wait()
        return await super().send(message)


class _BlockingAuthorizationEndpoint:
    def __init__(self) -> None:
        self.authorization_calls = 0
        self.delivery_calls = 0
        self.authorization_started = asyncio.Event()
        self.release_authorization = asyncio.Event()

    async def authorize_proactive_target(
        self,
        target: DeliveryTarget,
        *,
        credential: str,
    ) -> None:
        del target, credential
        self.authorization_calls += 1
        if self.authorization_calls == 1:
            self.authorization_started.set()
            await self.release_authorization.wait()

    async def deliver_proactively(
        self,
        intent: DeliveryIntent,
        *,
        credential: str,
    ) -> ProactiveDeliveryResult:
        del credential
        self.delivery_calls += 1
        return ProactiveDeliveryResult(
            delivery_id=intent.delivery_id,
            state=DeliverySubmissionState.ACCEPTED,
            destinations=(),
        )


def _text_payload(delivery_id: str) -> dict[str, object]:
    return {
        "deliveryId": delivery_id,
        "target": {
            "kind": "threadRoutes",
            "applicationInstanceId": "application",
            "nativeThreadId": "thread-1",
        },
        "content": [{"type": "text", "text": "bounded"}],
    }


class DeliveryIngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.thread_ref = ThreadRef("application", "thread-1")
        self.conversation_ref = ConversationRef("channel", "conversation-1")
        self.channel = _ReadingChannel()
        self.routes = InMemoryProjectionRouteRepository()
        await self.routes.put_projection_route(
            ThreadProjectionRoute(
                route_id="route-1",
                thread_ref=self.thread_ref,
                conversation_ref=self.conversation_ref,
                updated_at=datetime.now(UTC),
            )
        )
        self.authorizer = ScopedDeliveryAuthorizer()
        self.credential = await self.authorizer.issue(
            DeliveryPrincipal(
                principal_id="agent-task",
                allowed_threads=(self.thread_ref,),
            )
        )
        self.gateway = self._gateway_for_channel(self.channel)

    def _gateway_for_channel(
        self,
        channel: _ReadingChannel,
        *,
        submissions: InMemoryDeliverySubmissionRepository | None = None,
    ) -> ImAgentGateway:
        return ImAgentGateway(
            channels=[channel],
            applications=[
                FakeAgentApplicationAdapter(
                    application_instance_id="application",
                    project_mode=ProjectMode.FLAT,
                )
            ],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=self.routes,
                delivery_submissions=submissions,
            ),
            delivery_authorizer=self.authorizer,
            projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )

    def test_gateway_facade_uses_exact_owner_and_old_module_is_absent(self) -> None:
        self.assertIs(ProactiveDeliveryJsonHandler, OwnerProactiveDeliveryJsonHandler)
        with self.assertRaises(ModuleNotFoundError):
            __import__("imagent.delivery_ingress")

    def test_active_delivery_id_limit_is_keyword_only_positive_and_finite(self) -> None:
        parameter = inspect.signature(ProactiveDeliveryJsonHandler).parameters[
            "max_active_delivery_ids"
        ]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(parameter.default, 256)

        default = ProactiveDeliveryJsonHandler(
            self.gateway,
            staging_root="ingress",
        )
        self.assertEqual(default._locks.capacity, 256)

        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    ProactiveDeliveryJsonHandler(
                        self.gateway,
                        staging_root="ingress",
                        max_active_delivery_ids=invalid,  # type: ignore[arg-type]
                    )

    async def test_distinct_capacity_failure_precedes_auth_parse_staging_route_and_delivery(
        self,
    ) -> None:
        endpoint = _BlockingAuthorizationEndpoint()
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory, "ingress")
            handler = ProactiveDeliveryJsonHandler(
                endpoint,
                staging_root=staging_root,
                max_active_delivery_ids=1,
            )
            owner = asyncio.create_task(
                handler.handle(
                    _text_payload("delivery-exact"),
                    credential="owner-credential",
                )
            )
            await endpoint.authorization_started.wait()

            invalid_id = await handler.handle(
                _text_payload("   "),
                credential="invalid-id-credential",
            )
            self.assertEqual(invalid_id.status_code, 400)
            invalid_error = invalid_id.body["error"]
            assert isinstance(invalid_error, dict)
            self.assertEqual(invalid_error["code"], "invalid_delivery_request")

            secret = base64.b64encode(b"secret artifact bytes").decode()
            with (
                patch(
                    "imagent.gateway.delivery.proactive_ingress._parse_target",
                    side_effect=AssertionError("capacity must precede target parsing"),
                ) as parse_target,
                patch(
                    "imagent.gateway.delivery.proactive_ingress.validated_inline_artifact_size",
                    side_effect=AssertionError("capacity must precede base64 inspection"),
                ) as inspect_artifact,
                patch(
                    "imagent.gateway.delivery.proactive_ingress.create_inline_staging_directory",
                    side_effect=AssertionError("capacity must precede staging"),
                ) as create_staging,
                patch(
                    "imagent.gateway.delivery.proactive_ingress.stage_inline_artifacts",
                    side_effect=AssertionError("capacity must precede base64 decode"),
                ) as stage_artifacts,
            ):
                capacity = await asyncio.wait_for(
                    handler.handle(
                        {
                            "deliveryId": " delivery-exact ",
                            "target": {
                                "kind": "threadRoutes",
                                "applicationInstanceId": "application",
                                "nativeThreadId": "thread-1",
                            },
                            "content": [
                                {
                                    "type": "inlineArtifact",
                                    "attachmentId": "secret-artifact",
                                    "filename": "secret.bin",
                                    "mediaType": "application/octet-stream",
                                    "contentBase64": secret,
                                }
                            ],
                        },
                        credential="secret-credential",
                    ),
                    timeout=1.0,
                )

            self.assertEqual(capacity.status_code, 503)
            self.assertEqual(
                capacity.body,
                {
                    "error": {
                        "code": "delivery_ingress_capacity_exhausted",
                        "message": "proactive delivery ingress is at bounded capacity",
                    }
                },
            )
            response_text = repr(capacity.body)
            self.assertNotIn("delivery-exact", response_text)
            self.assertNotIn("secret-credential", response_text)
            self.assertNotIn(secret, response_text)
            self.assertEqual(endpoint.authorization_calls, 1)
            self.assertEqual(endpoint.delivery_calls, 0)
            self.assertEqual(parse_target.call_count, 0)
            self.assertEqual(inspect_artifact.call_count, 0)
            self.assertEqual(create_staging.call_count, 0)
            self.assertEqual(stage_artifacts.call_count, 0)
            self.assertFalse(staging_root.exists())

            endpoint.release_authorization.set()
            admitted = await owner

        self.assertEqual(admitted.status_code, 200)
        self.assertEqual(endpoint.delivery_calls, 1)

    async def test_inline_artifacts_are_staged_sent_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory, "ingress")
            handler = ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=staging_root,
            )
            response = await handler.handle(
                {
                    "deliveryId": "delivery-inline",
                    "target": {
                        "kind": "threadRoutes",
                        "applicationInstanceId": "application",
                        "nativeThreadId": "thread-1",
                    },
                    "content": [
                        {"type": "text", "text": "before", "format": "markdown"},
                        {
                            "type": "inlineArtifact",
                            "attachmentId": "artifact-1",
                            "filename": "one.bin",
                            "mediaType": "application/octet-stream",
                            "sizeBytes": 3,
                            "contentBase64": base64.b64encode(b"one").decode(),
                        },
                        {"type": "text", "text": "after"},
                        {
                            "type": "inlineArtifact",
                            "attachmentId": "artifact-2",
                            "filename": "two.bin",
                            "mediaType": "application/octet-stream",
                            "contentBase64": base64.b64encode(b"two").decode(),
                        },
                    ],
                },
                credential=self.credential,
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.body["state"], "accepted")
            destinations = response.body["destinations"]
            self.assertIsInstance(destinations, list)
            assert isinstance(destinations, list)
            self.assertNotIn("conversationRef", destinations[0])
            self.assertEqual(self.channel.artifact_bytes, [b"one", b"two"])
            sent_content = tuple(item for sent in self.channel.sent for item in sent.content)
            self.assertIsInstance(sent_content[0], TextContent)
            first_artifact = sent_content[1]
            self.assertIsInstance(first_artifact, AttachmentContent)
            assert isinstance(first_artifact, AttachmentContent)
            self.assertEqual(first_artifact.attachment_id, "artifact-1")
            self.assertIsInstance(sent_content[2], TextContent)
            second_artifact = sent_content[3]
            self.assertIsInstance(second_artifact, AttachmentContent)
            assert isinstance(second_artifact, AttachmentContent)
            self.assertEqual(second_artifact.attachment_id, "artifact-2")
            self.assertEqual(list(staging_root.glob("**/*")), [])

    async def test_same_delivery_id_joins_at_capacity_and_replays_one_send(self) -> None:
        channel = _GatedReadingChannel()
        gateway = self._gateway_for_channel(channel)
        with tempfile.TemporaryDirectory() as directory:
            handler = ProactiveDeliveryJsonHandler(
                gateway,
                staging_root=Path(directory, "ingress"),
                max_active_delivery_ids=1,
            )
            payload = _text_payload("delivery-joined")
            owner = asyncio.create_task(handler.handle(payload, credential=self.credential))
            await channel.first_send_started.wait()

            joiner = asyncio.create_task(handler.handle(payload, credential=self.credential))
            await asyncio.sleep(0)
            self.assertFalse(joiner.done())
            self.assertEqual(channel.send_attempts, 1)

            distinct = await handler.handle(
                _text_payload("delivery-distinct"),
                credential=self.credential,
            )
            self.assertEqual(distinct.status_code, 503)
            distinct_error = distinct.body["error"]
            assert isinstance(distinct_error, dict)
            self.assertEqual(
                distinct_error["code"],
                "delivery_ingress_capacity_exhausted",
            )

            channel.release_first_send.set()
            first, replay = await asyncio.gather(owner, joiner)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        replay_destinations = replay.body["destinations"]
        assert isinstance(replay_destinations, list)
        self.assertTrue(replay_destinations[0]["replayed"])
        self.assertEqual(channel.send_attempts, 1)
        self.assertEqual(handler._locks.active_key_count, 0)
        await gateway.stop()

    async def test_distinct_final_slot_race_admits_exactly_one_key(self) -> None:
        channel = _GatedReadingChannel()
        gateway = self._gateway_for_channel(channel)
        with tempfile.TemporaryDirectory() as directory:
            handler = ProactiveDeliveryJsonHandler(
                gateway,
                staging_root=Path(directory, "ingress"),
                max_active_delivery_ids=1,
            )
            requests = tuple(
                asyncio.create_task(
                    handler.handle(
                        _text_payload(delivery_id),
                        credential=self.credential,
                    )
                )
                for delivery_id in ("delivery-race-a", "delivery-race-b")
            )
            await channel.first_send_started.wait()
            await asyncio.sleep(0)
            channel.release_first_send.set()
            responses = await asyncio.gather(*requests)

        self.assertEqual(sorted(response.status_code for response in responses), [200, 503])
        capacity = next(response for response in responses if response.status_code == 503)
        capacity_error = capacity.body["error"]
        assert isinstance(capacity_error, dict)
        self.assertEqual(
            capacity_error["code"],
            "delivery_ingress_capacity_exhausted",
        )
        self.assertEqual(channel.send_attempts, 1)
        self.assertEqual(handler._locks.active_key_count, 0)
        await gateway.stop()

    async def test_cancelled_same_id_waiter_keeps_owner_key_until_completion_then_reuses_slot(
        self,
    ) -> None:
        channel = _GatedReadingChannel()
        gateway = self._gateway_for_channel(channel)
        with tempfile.TemporaryDirectory() as directory:
            handler = ProactiveDeliveryJsonHandler(
                gateway,
                staging_root=Path(directory, "ingress"),
                max_active_delivery_ids=1,
            )
            owner = asyncio.create_task(
                handler.handle(
                    _text_payload("delivery-owner"),
                    credential=self.credential,
                )
            )
            await channel.first_send_started.wait()
            waiter = asyncio.create_task(
                handler.handle(
                    _text_payload("delivery-owner"),
                    credential=self.credential,
                )
            )
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter

            still_full = await handler.handle(
                _text_payload("delivery-before-owner-completes"),
                credential=self.credential,
            )
            self.assertEqual(still_full.status_code, 503)

            channel.release_first_send.set()
            completed = await owner
            reused = await handler.handle(
                _text_payload("delivery-after-owner-completes"),
                credential=self.credential,
            )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(reused.status_code, 200)
        self.assertEqual(channel.send_attempts, 2)
        self.assertEqual(handler._locks.active_key_count, 0)
        await gateway.stop()

    async def test_capacity_exhaustion_is_a_bounded_service_response(self) -> None:
        gateway = ImAgentGateway(
            channels=[self.channel],
            applications=[
                FakeAgentApplicationAdapter(
                    application_instance_id="application",
                    project_mode=ProjectMode.FLAT,
                )
            ],
            repositories=GatewayRepositories(
                bindings=InMemoryBindingRepository(),
                projections=self.routes,
            ),
            limits=GatewayLimits(delivery_submission_max_records=1),
            delivery_authorizer=self.authorizer,
            projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )
        with tempfile.TemporaryDirectory() as directory:
            handler = ProactiveDeliveryJsonHandler(
                gateway,
                staging_root=Path(directory, "ingress"),
            )
            payload = {
                "target": {
                    "kind": "threadRoutes",
                    "applicationInstanceId": "application",
                    "nativeThreadId": "thread-1",
                },
                "content": [{"type": "text", "text": "bounded"}],
            }
            first = await handler.handle(
                {**payload, "deliveryId": "capacity-first"},
                credential=self.credential,
            )
            rejected = await handler.handle(
                {**payload, "deliveryId": "capacity-second"},
                credential=self.credential,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(rejected.status_code, 503)
        error = rejected.body["error"]
        self.assertIsInstance(error, dict)
        assert isinstance(error, dict)
        self.assertEqual(error["code"], "delivery_capacity_exhausted")
        self.assertEqual(len(self.channel.sent), 1)

    async def test_cancelled_ingress_joins_send_before_cleanup_and_records_unknown(self) -> None:
        channel = _BlockingReadingChannel()
        submissions = InMemoryDeliverySubmissionRepository()
        gateway = self._gateway_for_channel(channel, submissions=submissions)
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory, "ingress")
            handler = ProactiveDeliveryJsonHandler(
                gateway,
                staging_root=staging_root,
                max_active_delivery_ids=1,
            )
            request = asyncio.create_task(
                handler.handle(
                    {
                        "deliveryId": "delivery-cancelled",
                        "target": {
                            "kind": "threadRoutes",
                            "applicationInstanceId": "application",
                            "nativeThreadId": "thread-1",
                        },
                        "content": [
                            {
                                "type": "inlineArtifact",
                                "attachmentId": "artifact-1",
                                "filename": "one.bin",
                                "mediaType": "application/octet-stream",
                                "contentBase64": base64.b64encode(b"one").decode(),
                            }
                        ],
                    },
                    credential=self.credential,
                )
            )
            await channel.started_read.wait()
            assert channel.staged_path is not None
            self.assertTrue(channel.staged_path.exists())

            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request

            self.assertTrue(channel.cancelled_read.is_set())
            self.assertTrue(channel.path_existed_when_cancelled)
            self.assertEqual(list(staging_root.glob("**/*")), [])
            self.assertEqual(handler._locks.active_key_count, 0)

            reused = await handler.handle(
                _text_payload("delivery-after-cancel"),
                credential=self.credential,
            )
            self.assertEqual(reused.status_code, 200)
            self.assertEqual(handler._locks.active_key_count, 0)

        record = await submissions.get_delivery_submission(
            derive_delivery_submission_id(
                DeliverySubmissionOrigin.EXTERNAL,
                "agent-task",
                "delivery-cancelled",
            )
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertIs(record.destinations[0].state, DeliverySubmissionState.UNKNOWN)
        self.assertEqual(channel.send_attempts, 2)
        await gateway.stop()

    async def test_invalid_credential_is_rejected_before_artifact_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging_root = Path(directory, "ingress")
            handler = ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=staging_root,
            )
            response = await handler.handle(
                {
                    "deliveryId": "delivery-unauthorized",
                    "target": {
                        "kind": "threadRoutes",
                        "applicationInstanceId": "application",
                        "nativeThreadId": "thread-1",
                    },
                    "content": [
                        {
                            "type": "inlineArtifact",
                            "attachmentId": "artifact-1",
                            "filename": "one.bin",
                            "mediaType": "application/octet-stream",
                            "contentBase64": base64.b64encode(b"one").decode(),
                        }
                    ],
                },
                credential="invalid",
            )

            self.assertEqual(response.status_code, 403)
            self.assertFalse(staging_root.exists())
            self.assertEqual(self.channel.sent, [])

    async def test_reconstructed_handler_starts_empty_and_preserves_durable_replay(self) -> None:
        payload = {
            "deliveryId": "delivery-root-change",
            "target": {
                "kind": "threadRoutes",
                "applicationInstanceId": "application",
                "nativeThreadId": "thread-1",
            },
            "content": [
                {
                    "type": "inlineArtifact",
                    "attachmentId": "artifact-1",
                    "filename": "one.bin",
                    "mediaType": "application/octet-stream",
                    "contentBase64": base64.b64encode(b"one").decode(),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            first_handler = ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=Path(directory, "first"),
                max_active_delivery_ids=1,
            )
            first = await first_handler.handle(payload, credential=self.credential)
            reconstructed_handler = ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=Path(directory, "second"),
                max_active_delivery_ids=1,
            )
            self.assertEqual(reconstructed_handler._locks.active_key_count, 0)
            replay = await reconstructed_handler.handle(
                payload,
                credential=self.credential,
            )

        self.assertEqual(first.body["state"], "accepted")
        self.assertEqual(replay.body["state"], "accepted")
        destinations = replay.body["destinations"]
        assert isinstance(destinations, list)
        self.assertTrue(destinations[0]["replayed"])
        self.assertEqual(len(self.channel.sent), 1)


class DeliveryCliTests(unittest.TestCase):
    def test_reference_cli_posts_inline_artifact_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential_path = root / "credential"
            credential_path.write_text("scoped-token\n", encoding="utf-8")
            artifact_path = root / "result.txt"
            artifact_path.write_text("result", encoding="utf-8")

            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return {"deliveryId": "delivery-cli", "state": "accepted"}

            with patch("imagent.cli.send.httpx.Client") as client_factory:
                client = client_factory.return_value.__enter__.return_value
                client.post.return_value = Response()
                exit_code = send_main(
                    [
                        "--endpoint",
                        "http://127.0.0.1:8080/deliver",
                        "--credential-file",
                        str(credential_path),
                        "--delivery-id",
                        "delivery-cli",
                        "--application",
                        "application",
                        "--thread",
                        "thread-1",
                        "--text",
                        "done",
                        "--artifact",
                        str(artifact_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            client_factory.assert_called_once_with(trust_env=False)
            request = client.post.call_args.kwargs
            self.assertEqual(request["headers"]["Authorization"], "Bearer scoped-token")
            self.assertEqual(request["json"]["target"]["kind"], "threadRoutes")
            self.assertEqual(request["json"]["content"][1]["filename"], "result.txt")

    def test_reference_cli_rejects_remote_endpoint(self) -> None:
        exit_code = send_main(
            [
                "--endpoint",
                "https://example.com/deliver",
                "--credential-stdin",
                "--delivery-id",
                "delivery-cli",
                "--application",
                "application",
                "--thread",
                "thread-1",
                "--text",
                "done",
            ]
        )
        self.assertEqual(exit_code, 2)

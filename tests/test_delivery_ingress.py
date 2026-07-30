from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from imagent.bindings import InMemoryBindingRepository
from imagent.cli.send import main as send_main
from imagent.contracts import (
    AttachmentContent,
    AttachmentSourceKind,
    ChannelCapabilities,
    ConversationRef,
    DeliveryPrincipal,
    LocalPath,
    ProjectionPolicy,
    ProjectMode,
    SupportLevel,
    TextContent,
    ThreadProjectionRoute,
    ThreadRef,
)
from imagent.delivery_ingress import ProactiveDeliveryJsonHandler
from imagent.gateway import ImAgentGateway
from imagent.proactive_delivery import ScopedDeliveryAuthorizer
from imagent.projections import InMemoryProjectionRouteRepository
from imagent.testing import FakeAgentApplicationAdapter, FakeChannelAdapter


class _ReadingChannel(FakeChannelAdapter):
    def __init__(self) -> None:
        super().__init__("channel")
        self._capabilities = ChannelCapabilities(
            attachments=SupportLevel.NATIVE,
            attachment_sources=(AttachmentSourceKind.LOCAL_PATH,),
            max_attachment_count=4,
            max_attachment_size=1024,
        )
        self.artifact_bytes: list[bytes] = []

    async def send(self, message):
        attachments = [item for item in message.content if isinstance(item, AttachmentContent)]
        self.artifact_bytes = []
        for item in attachments:
            self.assert_local_path(item.source)
            assert isinstance(item.source, LocalPath)
            self.artifact_bytes.append(Path(item.source.path).read_bytes())
        return await super().send(message)

    @staticmethod
    def assert_local_path(source) -> None:
        if not isinstance(source, LocalPath):
            raise AssertionError("ingress must stage inline artifacts as LocalPath")


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
        self.gateway = ImAgentGateway(
            channels=[self.channel],
            applications=[
                FakeAgentApplicationAdapter(
                    application_instance_id="application",
                    project_mode=ProjectMode.FLAT,
                )
            ],
            bindings=InMemoryBindingRepository(),
            projections=self.routes,
            delivery_authorizer=self.authorizer,
            projection_policy=ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        )

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
            sent_content = self.channel.sent[0].content
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

    async def test_retry_is_stable_when_staging_root_changes(self) -> None:
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
            first = await ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=Path(directory, "first"),
            ).handle(payload, credential=self.credential)
            replay = await ProactiveDeliveryJsonHandler(
                self.gateway,
                staging_root=Path(directory, "second"),
            ).handle(payload, credential=self.credential)

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

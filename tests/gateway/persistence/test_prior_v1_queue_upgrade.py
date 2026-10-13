"""Opt-in upgrade proof using an actual pre-queue v1 source checkout.

Set IMAGENT_PRIOR_V1_SOURCE to a checkout root (tested with 88c6d141).
The old implementation, imported in a separate Python process, creates every
fixture through its store API. SQL below only inspects the disposable database;
it never fabricates an old schema by removing current tables or rewriting rows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from imagent.applications.contract import (
    ApplicationRef,
    InputContinuationPreference,
    ProjectRef,
    QueuedInputReceipt,
    QueuedInputState,
    ThreadRef,
    WorkspaceIdentity,
)
from imagent.applications.requests import ApprovalResponseShape
from imagent.gateway.persistence.effects import (
    ActionIdentity,
    EffectPhase,
    derive_action_fingerprint,
)
from imagent.gateway.persistence.repository_contracts import IdempotencyClaimStatus
from imagent.gateway.persistence.sqlite_store import SQLiteGatewayStore
from imagent.gateway.persistence.state_contracts import QueuedInputOrigin, RequestRouteState
from imagent.gateway.persistence.store import (
    GatewayNamespaceConflict,
    QueuedInputFenceConflict,
    WorkspaceIdentityConflict,
)
from imagent.interaction.messages import ConversationRef

_PRIOR_SOURCE_ENV = "IMAGENT_PRIOR_V1_SOURCE"

# Deliberately standalone: no imports from this test, current src, or fixtures.
_SEED_PRIOR_V1 = r"""
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import imagent.gateway.persistence.sqlite_store as implementation
from imagent.applications.contract import (
    ApplicationRef, ProjectRef, ThreadRef, TurnRef, WorkspaceIdentity,
)
from imagent.applications.requests import ApprovalResponseShape, RequestRef
from imagent.gateway.persistence.effects import (
    ActionIdentity, BindingTarget, EffectCategory, StoreMutationPlan,
    StoreMutationRequest, derive_action_fingerprint,
)
from imagent.gateway.persistence.state_contracts import (
    ConversationBinding, RequestRouteCorrelation, RequestRouteState,
    ThreadProjectionRoute, TurnReplyCorrelation,
)
from imagent.interaction.messages import ConversationRef

async def seed():
    source = Path(sys.argv[2]).resolve()
    loaded = Path(implementation.__file__).resolve()
    assert loaded == source / 'src/imagent/gateway/persistence/sqlite_store.py', loaded
    store = implementation.SQLiteGatewayStore(sys.argv[1])
    try:
        session = await store.acquire_runtime(
            gateway_id='prior-gateway', owner_token='prior-runtime',
            lease_duration_seconds=60,
        )
        assert not hasattr(session, 'fence_queued_input'), 'source is not pre-queue v1'
        conversation = ConversationRef('channel', 'conversation')
        selected = ConversationRef('channel', 'selected-only')
        application = ApplicationRef('application')
        project = ProjectRef('application', 'project')
        thread = ThreadRef(project, 'thread')
        turn = TurnRef(thread, 'prior-turn')
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await session.check_workspace_identities((WorkspaceIdentity(project, 'c' * 64),))
        await session.put(ConversationBinding(selected, application, project))
        await session.put(ConversationBinding(conversation, application, project))
        await session.put(ConversationBinding(conversation, application, project, thread))
        await session.put_projection_route(
            ThreadProjectionRoute('prior-route', thread, conversation, 'prior-topic')
        )
        await session.put_turn_reply_correlation(
            TurnReplyCorrelation('prior-reply', turn, 'prior-client', conversation,
                                 'prior-message', now)
        )
        await session.put_request_correlation(RequestRouteCorrelation(
            'prior-request-route', RequestRef(application, 'epoch-1:request-1'),
            turn, conversation, 'prior-delivery',
            ApprovalResponseShape(('approve_once', 'decline')),
            RequestRouteState.OPEN, now, now,
        ))
        for message in ('prior-completed', 'prior-unknown'):
            await session.claim('inbound:channel', 'conversation:' + message,
                                owner_token='prior-inbound')
        await session.complete('inbound:channel', 'conversation:prior-completed',
                               owner_token='prior-inbound')
        await session.mark_side_effect_started(
            'inbound:channel', 'conversation:prior-unknown', owner_token='prior-inbound'
        )
        fingerprint = derive_action_fingerprint(
            ActionIdentity('prior-gateway', 'principal', 'create_thread',
                           'prior-native', conversation),
            (('purpose', 'synthetic'),),
        )
        await session.reserve_effect(fingerprint, category=EffectCategory.NATIVE,
                                     native_phase_id='prior-native-phase')
        await session.mark_native_side_effect_started(fingerprint)
        store_fingerprint = derive_action_fingerprint(
            ActionIdentity('prior-gateway', 'principal', 'conversation.select',
                           'prior-select', selected),
            (('purpose', 'synthetic'),),
        )
        await session.commit_store_mutation(StoreMutationRequest(
            store_fingerprint,
            StoreMutationPlan(conversation_ref=selected,
                              binding_target=BindingTarget(selected, application, project),
                              expected_generation=1),
        ))
        await session.release_runtime()
    finally:
        await store.close()
    print(json.dumps({'source_module': str(loaded), 'prior_epoch': 1}))

asyncio.run(seed())
"""


def _snapshot(path: Path) -> tuple[dict[str, tuple[object, ...]], dict[str, tuple]]:
    """Inspect schema and raw evidence, including fields without public getters."""
    with closing(sqlite3.connect(path)) as connection:
        schema = {
            row[1]: row
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
            )
        }
        tables = [name for name, row in schema.items() if row[0] == "table"]
        rows = {
            name: tuple(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"'))
            for name in tables
        }
        return schema, rows


@unittest.skipUnless(os.environ.get(_PRIOR_SOURCE_ENV), "requires an actual prior-v1 checkout")
class PriorV1QueueUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_prior_v1_store_preserved_and_queue_added(self) -> None:
        source = Path(os.environ[_PRIOR_SOURCE_ENV]).resolve()
        self.assertTrue((source / "src/imagent/gateway/persistence/sqlite_store.py").is_file())
        with tempfile.TemporaryDirectory(prefix="prior-v1-upgrade-") as directory:
            path = Path(directory) / "synthetic.sqlite3"
            environment = dict(os.environ, PYTHONPATH=str(source / "src"))
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [sys.executable, "-c", _SEED_PRIOR_V1, str(path), str(source)],
                cwd=directory,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["source_module"],
                str(source / "src/imagent/gateway/persistence/sqlite_store.py"),
            )
            old_schema, old_rows = _snapshot(path)
            self.assertNotIn("queued_input_correlations", old_rows)
            for table in (
                "conversation_bindings",
                "conversation_binding_generations",
                "idempotency_records",
                "thread_projection_routes",
                "turn_reply_correlations",
                "request_route_correlations",
                "gateway_workspace_identities",
                "gateway_effect_receipts",
                "gateway_namespace",
                "gateway_runtime_lease",
            ):
                self.assertTrue(old_rows[table], table)

            # Zero stale age proves unknown side effects remain protected even
            # when an ordinary abandoned in-flight claim would be reclaimable.
            store = SQLiteGatewayStore(path, stale_claim_after_seconds=0)
            try:
                upgraded_schema, upgraded_rows = _snapshot(path)
                self.assertEqual(set(upgraded_rows) - set(old_rows), {"queued_input_correlations"})
                for name, definition in old_schema.items():
                    self.assertEqual(upgraded_schema[name], definition, name)
                for name, rows in old_rows.items():
                    self.assertEqual(upgraded_rows[name], rows, name)
                self.assertEqual(upgraded_rows["queued_input_correlations"], ())
                for operation in ("insert", "update", "delete"):
                    self.assertIn(
                        f"imagent_runtime_fence_queued_input_correlations_{operation}",
                        upgraded_schema,
                    )
                with self.assertRaises(GatewayNamespaceConflict):
                    await store.acquire_runtime(
                        gateway_id="different-gateway",
                        owner_token="new-runtime",
                        lease_duration_seconds=60,
                    )
                session = await store.acquire_runtime(
                    gateway_id="prior-gateway",
                    owner_token="new-runtime",
                    lease_duration_seconds=60,
                )
                self.assertEqual(session.lease.epoch, 2)
                conversation = ConversationRef("channel", "conversation")
                project = ProjectRef("application", "project")
                thread = ThreadRef(project, "thread")
                binding = await session.get(conversation)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding.thread_ref, thread)
                self.assertEqual(binding.generation, 2)
                self.assertEqual(await session.get_binding_generation(conversation), 2)
                selected = await session.get(ConversationRef("channel", "selected-only"))
                self.assertIsNotNone(selected)
                assert selected is not None
                self.assertEqual(selected.application_ref, ApplicationRef("application"))
                self.assertEqual(selected.project_ref, project)
                self.assertIsNone(selected.thread_ref)
                self.assertEqual(len(await session.list_projection_routes(thread)), 1)
                reply = await session.get_turn_reply_correlation(thread, "prior-turn")
                self.assertIsNotNone(reply)
                assert reply is not None
                self.assertEqual(reply.reply_to_message_id, "prior-message")
                requests = await session.list_request_correlations(thread_ref=thread)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].state, RequestRouteState.OPEN)
                self.assertEqual(
                    requests[0].response_shape, ApprovalResponseShape(("approve_once", "decline"))
                )
                await session.check_workspace_identities((WorkspaceIdentity(project, "c" * 64),))
                with self.assertRaises(WorkspaceIdentityConflict):
                    await session.check_workspace_identities(
                        (WorkspaceIdentity(project, "d" * 64),)
                    )
                fingerprint = derive_action_fingerprint(
                    ActionIdentity(
                        "prior-gateway", "principal", "create_thread", "prior-native", conversation
                    ),
                    (("purpose", "synthetic"),),
                )
                effect = await session.get_effect_receipt(fingerprint)
                self.assertIsNotNone(effect)
                assert effect is not None
                self.assertEqual(effect.phase, EffectPhase.NATIVE_SIDE_EFFECT_STARTED)
                self.assertFalse((await session.mark_native_side_effect_started(fingerprint))[1])
                self.assertEqual(await session.list_queued_inputs(), ())
                for message_id, expected_status in (
                    ("prior-completed", IdempotencyClaimStatus.ALREADY_COMPLETED),
                    ("prior-unknown", IdempotencyClaimStatus.IN_FLIGHT),
                ):
                    self.assertEqual(
                        await session.claim(
                            "inbound:channel",
                            "conversation:" + message_id,
                            owner_token="new-inbound",
                        ),
                        expected_status,
                    )
                    origin = QueuedInputOrigin(
                        conversation,
                        message_id,
                        "a" * 64,
                        thread,
                        binding.generation,
                        "native-" + message_id,
                        "b" * 64,
                        InputContinuationPreference.QUEUE,
                    )
                    with self.assertRaises(QueuedInputFenceConflict):
                        await session.fence_queued_input(
                            origin,
                            claim_scope="inbound:channel",
                            claim_key="conversation:" + message_id,
                            owner_token="new-inbound",
                        )
                    self.assertIsNone(await session.get_queued_input(conversation, message_id))
                origin = QueuedInputOrigin(
                    conversation,
                    "new-message",
                    "a" * 64,
                    thread,
                    binding.generation,
                    "native-new-message",
                    "b" * 64,
                    InputContinuationPreference.QUEUE,
                )
                self.assertEqual(
                    await session.claim(
                        "inbound:channel", "conversation:new-message", owner_token="new-inbound"
                    ),
                    IdempotencyClaimStatus.ACQUIRED,
                )
                fenced = await session.fence_queued_input(
                    origin,
                    claim_scope="inbound:channel",
                    claim_key="conversation:new-message",
                    owner_token="new-inbound",
                )
                self.assertIsNone(fenced.receipt)
                receipt = QueuedInputReceipt(
                    thread,
                    origin.client_message_id,
                    "new-queued-item",
                    QueuedInputState.QUEUED,
                    input_fingerprint=origin.input_fingerprint,
                )
                queued = await session.merge_queued_input_receipt(origin, receipt)
                await session.release_runtime()
            finally:
                await store.close()

            reopened = SQLiteGatewayStore(path)
            try:
                recovered = await reopened.acquire_runtime(
                    gateway_id="prior-gateway",
                    owner_token="restart-runtime",
                    lease_duration_seconds=60,
                )
                self.assertEqual(
                    await recovered.get_queued_input(conversation, "new-message"), queued
                )
                self.assertEqual(await recovered.list_queued_inputs(), (queued,))
                await recovered.release_runtime()
            finally:
                await reopened.close()
            _, final_rows = _snapshot(path)
            for name, rows in old_rows.items():
                if name != "gateway_runtime_lease":
                    self.assertTrue(set(rows).issubset(final_rows[name]), name)


if __name__ == "__main__":
    unittest.main()

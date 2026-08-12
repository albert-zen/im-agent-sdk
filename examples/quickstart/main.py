"""Run the minimal inbound -> Application -> outbound public SDK vertical."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from imagent import Gateway, GatewayLimits, ProjectionPolicy, SQLiteGatewayStore, Succeeded
from imagent.applications import ProjectRef, ThreadRef
from imagent.interaction.messages import ConversationRef

from .adapters import LocalChannel, LocalEchoApplication, outbound_text


@dataclass(frozen=True, slots=True)
class QuickstartResult:
    response: str
    database_path: Path


async def run_quickstart(*, workspace: Path, state_dir: Path) -> QuickstartResult:
    workspace = workspace.resolve()
    state_dir = state_dir.resolve()
    if not workspace.is_dir():
        raise ValueError("--workspace must name an existing directory")
    state_dir.mkdir(parents=True, exist_ok=True)
    database_path = state_dir / "bridge.sqlite3"
    if database_path.exists():
        raise RuntimeError(
            "this demo Application is process-local; use an empty --state-dir for each run"
        )

    channel = LocalChannel()
    application = LocalEchoApplication()
    store = SQLiteGatewayStore(database_path, max_effect_receipts=32)
    gateway = Gateway(
        gateway_id="quickstart",
        channels=[channel],
        applications=[application],
        store=store,
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        limits=GatewayLimits(
            startup_buffer_max_pending=8,
            turn_acceptance_event_max_pending=8,
            conversation_serialization_max_active_keys=8,
            idempotency_max_records=32,
            projection_max_active_threads=2,
        ),
    )
    conversation_ref = ConversationRef(channel.channel_instance_id, "local-conversation")
    actor = "local-user"

    async with gateway:
        actions = gateway.actions(conversation_ref, actor=actor)
        project_result = await actions.create_and_select_project(
            application.summary.ref,
            cwd=str(workspace),
            action_id="quickstart:create-project:1",
        )
        if not isinstance(project_result, Succeeded):
            raise RuntimeError(f"Project workflow failed: {type(project_result).__name__}")
        project_ref = project_result.value.ref
        if not isinstance(project_ref, ProjectRef):
            raise RuntimeError("Project workflow returned an unexpected public reference")
        thread_result = await actions.create_and_bind_thread(
            project_ref,
            title="Quickstart thread",
            action_id="quickstart:create-thread:1",
        )
        if not isinstance(thread_result, Succeeded):
            raise RuntimeError(f"Thread workflow failed: {type(thread_result).__name__}")
        if not isinstance(thread_result.value.ref, ThreadRef):
            raise RuntimeError("Thread workflow returned an unexpected public reference")

        await channel.receive_text(
            conversation_ref,
            actor=actor,
            message_id="quickstart:message:1",
            text="hello from IM",
        )
        delivered = await channel.wait_for_delivery()

    return QuickstartResult(response=outbound_text(delivered), database_path=database_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(run_quickstart(workspace=args.workspace, state_dir=args.state_dir))
    print(result.response)
    print(f"bridge state: {result.database_path}")
    print("Agent transcript/state owner: LocalEchoApplication (process-local demo)")


if __name__ == "__main__":
    main()

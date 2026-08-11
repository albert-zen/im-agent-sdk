"""Executable golden path for the neutral reference consumer."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar

from imagent import ProjectionPolicy, SQLiteGatewayStore, Succeeded
from imagent.applications.contract import ProjectRef, ThreadRef
from imagent.interaction.messages import ConversationRef, OutboundMessage, TextContent

from .gateway import ReferenceConsumer, build_reference_consumer
from .interaction import ReferenceConversation

TRef = TypeVar("TRef", ProjectRef, ThreadRef)

_SQLITE_INSPECTION_MAX_FILES = 8
_SQLITE_INSPECTION_MAX_FILE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReferenceReport:
    projection_policy: ProjectionPolicy
    project_ref: ProjectRef
    first_thread_ref: ThreadRef
    second_thread_ref: ThreadRef
    project_count: int
    thread_count: int
    conversation_count: int
    command_count: int
    initial_thread_conversations: tuple[ConversationRef, ...]
    shared_thread_conversations: tuple[ConversationRef, ...]
    switched_old_thread_conversations: tuple[ConversationRef, ...]
    switched_new_thread_conversations: tuple[ConversationRef, ...]
    switched_back_conversations: tuple[ConversationRef, ...]
    worker_max_active: tuple[int, ...]
    worker_subscription_calls: tuple[int, ...]
    recovered_conversations: tuple[ConversationRef, ...]
    recovered_delivery_count: int
    reconstructed_bindings: bool
    reconstructed_receipts: bool
    recovery_redispatched_input: bool
    sqlite_files_inspected: int
    sqlite_table_count: int
    sqlite_leak_free: bool
    diagnostics_schema_version: int
    diagnostics_size: int
    diagnostics_authoritative: bool
    adapters_stopped: bool
    active_workers_after_shutdown: int
    registry_active_after_shutdown: int
    owned_tasks_after_shutdown: int


def _message_text(message: OutboundMessage) -> str:
    return " ".join(content.text for content in message.content if isinstance(content, TextContent))


def _destinations(messages: tuple[OutboundMessage, ...]) -> tuple[ConversationRef, ...]:
    return tuple(
        sorted(
            (message.conversation_ref for message in messages),
            key=lambda ref: (ref.channel_instance_id, ref.native_conversation_id),
        )
    )


def _required_ref(result: object, expected: type[TRef]) -> TRef:
    if not isinstance(result, Succeeded) or not isinstance(result.value.ref, expected):
        raise RuntimeError("reference workflow returned a non-success outcome")
    return result.value.ref


def _inspect_sqlite_files(
    database_path: Path,
    *,
    forbidden_values: tuple[str, ...],
) -> int:
    """Inspect the bounded database file and every currently present sidecar."""

    database_files = tuple(
        sorted(
            path for path in database_path.parent.glob(f"{database_path.name}*") if path.is_file()
        )
    )
    if database_path not in database_files:
        raise AssertionError("SQLite recovery database was not created")
    if len(database_files) > _SQLITE_INSPECTION_MAX_FILES:
        raise AssertionError("SQLite recovery produced too many sidecar files")
    for path in database_files:
        if path.stat().st_size > _SQLITE_INSPECTION_MAX_FILE_BYTES:
            raise AssertionError("SQLite bridge persistence exceeded the inspection bound")
        persisted = path.read_bytes()
        for value in forbidden_values:
            if value.encode("utf-8") in persisted:
                raise AssertionError("SQLite bridge persistence retained authority-owned data")
    return len(database_files)


def _inspect_sqlite_bridge_state(
    database_path: Path,
    *,
    forbidden_values: tuple[str, ...],
) -> tuple[int, int]:
    """Inspect bounded bridge rows and every database sidecar after shutdown."""

    database_file_count = _inspect_sqlite_files(
        database_path,
        forbidden_values=forbidden_values,
    )

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise AssertionError("SQLite recovery database failed its integrity check")
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        required_tables = {
            "conversation_bindings",
            "gateway_effect_receipts",
            "idempotency_records",
            "thread_projection_routes",
        }
        if not required_tables.issubset(tables):
            raise AssertionError("SQLite bridge persistence is missing required projections")
        if connection.execute("SELECT COUNT(*) FROM conversation_bindings").fetchone() != (2,):
            raise AssertionError("SQLite binding reconstruction evidence is incomplete")
        if connection.execute("SELECT COUNT(*) FROM thread_projection_routes").fetchone() != (3,):
            raise AssertionError("SQLite route reconstruction evidence is incomplete")
        if connection.execute(
            "SELECT COUNT(*) FROM thread_projection_routes "
            "WHERE checkpoint_agent_item_id IS NOT NULL"
        ).fetchone() != (2,):
            raise AssertionError("SQLite destination checkpoints were not reconstructed")
        if connection.execute(
            "SELECT COUNT(*) FROM thread_projection_routes AS r "
            "JOIN conversation_bindings AS b "
            "ON b.channel_instance_id = r.channel_instance_id "
            "AND b.native_conversation_id = r.native_conversation_id "
            "AND b.application_instance_id = r.application_instance_id "
            "AND b.project_id = r.project_id AND b.thread_id = r.thread_id"
        ).fetchone() != (2,):
            raise AssertionError("SQLite active route authority was not reconstructed")
        if (
            connection.execute(
                "SELECT COUNT(*) FROM gateway_effect_receipts WHERE phase = 'terminal'"
            ).fetchone()[0]
            < 5
        ):
            raise AssertionError("SQLite terminal receipt evidence is incomplete")
    finally:
        connection.close()
    return database_file_count, len(tables)


async def _ordinary_round_trip(
    consumer: ReferenceConsumer,
    conversation: ReferenceConversation,
    *,
    message_id: str,
    text: str,
    expected_count: int,
) -> tuple[OutboundMessage, ...]:
    after = len(consumer.channel.sent)
    await conversation.receive_text(message_id=message_id, text=text)
    await consumer.channel.wait_for_text(
        f"Neutral response: {text}",
        after=after,
        count=expected_count,
    )
    await asyncio.sleep(0)
    delivered = tuple(
        message
        for message in consumer.channel.sent[after:]
        if _message_text(message) == f"Neutral response: {text}"
    )
    if len(delivered) != expected_count:
        raise AssertionError("ordinary input produced duplicate or missing deliveries")
    return delivered


async def run_reference_consumer(reference_workspace: str) -> ReferenceReport:
    """Run the same bounded public entry point used by source and wheel tests."""

    database_path = Path(reference_workspace) / "reference-gateway.sqlite3"
    consumer = build_reference_consumer(
        store=SQLiteGatewayStore(database_path, max_effect_receipts=64),
    )
    gateway = consumer.gateway
    application = consumer.application
    conversation_a = consumer.channel.conversation(
        "conversation-a",
        authenticated_actor="reference-user-a",
    )
    conversation_b = consumer.channel.conversation(
        "conversation-b",
        authenticated_actor="reference-user-b",
    )
    tasks_before = set(asyncio.all_tasks())

    command_count = 0
    diagnostics_size = 0
    diagnostics_schema_version = 0
    diagnostics_authoritative = True
    project_count = 0
    thread_count = 0
    initial: tuple[OutboundMessage, ...] = ()
    shared: tuple[OutboundMessage, ...] = ()
    switched_old: tuple[OutboundMessage, ...] = ()
    switched_new: tuple[OutboundMessage, ...] = ()
    switched_back: tuple[OutboundMessage, ...] = ()
    recovered: tuple[OutboundMessage, ...] = ()
    reconstructed_bindings = False
    reconstructed_receipts = False
    recovery_redispatched_input = True
    sqlite_files_inspected = 0
    sqlite_table_count = 0
    sqlite_leak_free = False

    async with gateway:
        actions_a = gateway.actions(
            conversation_a.ref,
            actor=conversation_a.authenticated_actor,
        )
        actions_b = gateway.actions(
            conversation_b.ref,
            actor=conversation_b.authenticated_actor,
        )

        discovered = await actions_a.list_applications()
        if not isinstance(discovered, Succeeded) or len(discovered.value) != 1:
            raise RuntimeError("reference Application discovery did not return one result")
        application_ref = discovered.value[0].ref
        application_actions = gateway.application(
            application_ref,
            principal=conversation_a.authenticated_actor,
        )
        application_read = await application_actions.get_application()
        if (
            not isinstance(application_read, Succeeded)
            or application_read.value.ref != application_ref
            or application_ref != application.ref
        ):
            raise RuntimeError("reference Application discovery identity was not stable")

        project_result = await actions_a.create_and_select_project(
            application_ref,
            cwd=reference_workspace,
            display_name="Reference workspace",
            action_id="reference:create-project:1",
        )
        project_ref = _required_ref(project_result, ProjectRef)
        replayed_project = _required_ref(
            await actions_a.create_and_select_project(
                application.ref,
                cwd=reference_workspace,
                display_name="Reference workspace",
                action_id="reference:create-project:1",
            ),
            ProjectRef,
        )
        if replayed_project != project_ref:
            raise AssertionError("stable Project workflow identity did not replay")

        first_thread_result = await actions_a.create_and_bind_thread(
            project_ref,
            title="Shared reference Thread",
            action_id="reference:create-thread:1",
        )
        first_thread_ref = _required_ref(first_thread_result, ThreadRef)
        replayed_thread = _required_ref(
            await actions_a.create_and_bind_thread(
                project_ref,
                title="Shared reference Thread",
                action_id="reference:create-thread:1",
            ),
            ThreadRef,
        )
        if replayed_thread != first_thread_ref:
            raise AssertionError("stable Thread workflow identity did not replay")

        initial = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:initial",
            text="initial-thread-turn",
            expected_count=1,
        )
        if _destinations(initial) != (conversation_a.ref,):
            raise AssertionError("the initial Thread output must reach only Conversation A")

        selected_b = await actions_b.select_project(
            project_ref,
            action_id="reference:conversation-b:select-project",
        )
        if not isinstance(selected_b, Succeeded):
            raise RuntimeError("Conversation B could not select the shared Project")
        bound_b = await actions_b.bind_thread(
            first_thread_ref,
            action_id="reference:conversation-b:bind-thread",
            expected_generation=selected_b.value.binding_generation,
        )
        if not isinstance(bound_b, Succeeded):
            raise RuntimeError("Conversation B could not bind the shared Thread")

        for message_id, command in (
            ("reference:command:help", "/help"),
            ("reference:command:about", "/about"),
        ):
            before = len(consumer.channel.sent)
            await conversation_a.receive_text(message_id=message_id, text=command)
            if len(consumer.channel.sent) != before + 1:
                raise AssertionError("one command invocation must produce one bounded output")
            command_output = _message_text(consumer.channel.sent[-1])
            if not command_output or len(command_output) > 1_024:
                raise AssertionError("command output must be non-empty and bounded")
            if command == "/about" and command_output != (
                "Neutral reference consumer: Channel, Gateway, and Application composed "
                "through public SDK contracts."
            ):
                raise AssertionError("the injected read-only status service did not run")
            command_count += 1

        shared = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:shared",
            text="shared-thread-turn",
            expected_count=2,
        )
        if _destinations(shared) != (conversation_a.ref, conversation_b.ref):
            raise AssertionError("both Conversations must observe the shared Thread")

        before_new = len(consumer.channel.sent)
        await conversation_a.receive_text(
            message_id="reference:command:new-thread",
            text="/new Switched reference Thread",
        )
        if len(consumer.channel.sent) != before_new + 1:
            raise AssertionError("the effectful common command must produce one output")
        if not _message_text(consumer.channel.sent[-1]).startswith("Created thread"):
            raise AssertionError("the effectful common workflow command did not complete")
        command_count += 1
        binding_a = await actions_a.get_binding()
        if binding_a is None or binding_a.thread_ref is None:
            raise AssertionError("the effectful command did not bind its created Thread")
        second_thread_ref = binding_a.thread_ref
        if second_thread_ref == first_thread_ref:
            raise AssertionError("the effectful command must create a distinct Thread")

        switched_old = await _ordinary_round_trip(
            consumer,
            conversation_b,
            message_id="reference:message:old-after-switch",
            text="old-thread-after-switch",
            expected_count=1,
        )
        if _destinations(switched_old) != (conversation_b.ref,):
            raise AssertionError("Thread-1 output leaked to the switched Conversation")

        switched_new = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:new-after-switch",
            text="new-thread-after-switch",
            expected_count=1,
        )
        if _destinations(switched_new) != (conversation_a.ref,):
            raise AssertionError("Thread-2 output leaked to the other Conversation")

        rebound_a = await actions_a.bind_thread(
            first_thread_ref,
            action_id="reference:conversation-a:switch-back",
            expected_generation=binding_a.generation,
        )
        if not isinstance(rebound_a, Succeeded):
            raise RuntimeError("Conversation A could not switch back to Thread 1")
        switched_back = await _ordinary_round_trip(
            consumer,
            conversation_a,
            message_id="reference:message:after-switch-back",
            text="thread-one-after-switch-back",
            expected_count=2,
        )
        if _destinations(switched_back) != (conversation_a.ref, conversation_b.ref):
            raise AssertionError("switch-back did not restore the shared destinations")

        projects = await actions_a.list_projects(application.ref)
        threads = await actions_a.list_threads(project_ref)
        if not isinstance(projects, Succeeded) or not isinstance(threads, Succeeded):
            raise RuntimeError("bounded resource reads did not succeed")
        project_count = len(projects.value.items)
        thread_count = len(threads.value.items)

        diagnostics = gateway.diagnostics()
        rendered_diagnostics = repr(diagnostics)
        diagnostics_size = len(rendered_diagnostics)
        diagnostics_schema_version = diagnostics.schema_version
        diagnostics_authoritative = diagnostics.authoritative
        if diagnostics_size > 4_096:
            raise AssertionError("Gateway diagnostics exceeded the reference bound")
        for secret in (
            reference_workspace,
            conversation_a.ref.native_conversation_id,
            conversation_b.ref.native_conversation_id,
            first_thread_ref.thread_id,
            second_thread_ref.thread_id,
            "initial-thread-turn",
            "shared-thread-turn",
        ):
            if secret in rendered_diagnostics:
                raise AssertionError("Gateway diagnostics exposed scoped or content data")

    persistence_markers = {
        "native_payload": "native-payload-must-not-persist",
        "request_snapshot": "request-body-must-not-persist",
        "media": "media-bytes-must-not-persist",
        "artifact": "artifact-bytes-must-not-persist",
        "credential": "credential-must-not-persist",
    }
    forbidden_persistence_values = (
        reference_workspace,
        "Reference workspace",
        "initial-thread-turn",
        "shared-thread-turn",
        "old-thread-after-switch",
        "new-thread-after-switch",
        "thread-one-after-switch-back",
        "authoritative-output-while-gateway-stopped",
        *persistence_markers.values(),
    )
    await application.receive_native_text(
        first_thread_ref,
        client_message_id="reference:native:while-gateway-stopped",
        text="authoritative-output-while-gateway-stopped",
        metadata=persistence_markers,
    )
    dispatched_before_restart = application.input_dispatch_calls
    creation_calls_before_restart = (
        application.project_creation_calls,
        application.thread_creation_calls,
    )

    restarted = build_reference_consumer(
        application=application,
        store=SQLiteGatewayStore(database_path, max_effect_receipts=64),
    )
    restarted_conversation_a = restarted.channel.conversation(
        "conversation-a",
        authenticated_actor="reference-user-a",
    )
    restarted_conversation_b = restarted.channel.conversation(
        "conversation-b",
        authenticated_actor="reference-user-b",
    )
    async with restarted.gateway:
        restarted_actions_a = restarted.gateway.actions(
            restarted_conversation_a.ref,
            actor=restarted_conversation_a.authenticated_actor,
        )
        restarted_actions_b = restarted.gateway.actions(
            restarted_conversation_b.ref,
            actor=restarted_conversation_b.authenticated_actor,
        )
        await restarted.channel.wait_for_text(
            "Neutral response: authoritative-output-while-gateway-stopped",
            count=2,
        )
        await asyncio.sleep(0)
        recovered = tuple(restarted.channel.sent)
        if len(recovered) != 2 or _destinations(recovered) != (
            restarted_conversation_a.ref,
            restarted_conversation_b.ref,
        ):
            raise AssertionError("SQLite recovery duplicated or omitted authoritative output")

        restored_a = await restarted_actions_a.get_binding()
        restored_b = await restarted_actions_b.get_binding()
        reconstructed_bindings = bool(
            restored_a is not None
            and restored_b is not None
            and restored_a.thread_ref == first_thread_ref
            and restored_b.thread_ref == first_thread_ref
        )
        if not reconstructed_bindings:
            raise AssertionError("SQLite restart did not reconstruct both bindings")

        replayed_project_after_restart = _required_ref(
            await restarted_actions_a.create_and_select_project(
                application.ref,
                cwd=reference_workspace,
                display_name="Reference workspace",
                action_id="reference:create-project:1",
            ),
            ProjectRef,
        )
        replayed_thread_after_restart = _required_ref(
            await restarted_actions_a.create_and_bind_thread(
                project_ref,
                title="Shared reference Thread",
                action_id="reference:create-thread:1",
            ),
            ThreadRef,
        )
        reconstructed_receipts = (
            replayed_project_after_restart == project_ref
            and replayed_thread_after_restart == first_thread_ref
            and creation_calls_before_restart
            == (
                application.project_creation_calls,
                application.thread_creation_calls,
            )
        )
        if not reconstructed_receipts:
            raise AssertionError("SQLite restart did not replay terminal workflow receipts")
        recovery_redispatched_input = application.input_dispatch_calls != dispatched_before_restart
        if recovery_redispatched_input:
            raise AssertionError("projection recovery redispatched authoritative native input")
        sqlite_files_inspected = _inspect_sqlite_files(
            database_path,
            forbidden_values=forbidden_persistence_values,
        )
        if sqlite_files_inspected < 3:
            raise AssertionError("SQLite WAL and shared-memory sidecars were not inspected")

    await asyncio.sleep(0)
    active_workers = sum(
        application.active_observation_workers(thread_ref)
        for thread_ref in (first_thread_ref, second_thread_ref)
    )
    registry_active = sum(
        registry.diagnostic_facts().active_handler_count
        for registry in (consumer.registry, restarted.registry)
    )
    closed_files_inspected, sqlite_table_count = _inspect_sqlite_bridge_state(
        database_path,
        forbidden_values=forbidden_persistence_values,
    )
    sqlite_files_inspected = max(sqlite_files_inspected, closed_files_inspected)
    sqlite_leak_free = True
    current = asyncio.current_task()
    owned_tasks = tuple(
        task
        for task in asyncio.all_tasks()
        if task is not current and task not in tasks_before and not task.done()
    )
    return ReferenceReport(
        projection_policy=ProjectionPolicy.FOREGROUND_ONLY,
        project_ref=project_ref,
        first_thread_ref=first_thread_ref,
        second_thread_ref=second_thread_ref,
        project_count=project_count,
        thread_count=thread_count,
        conversation_count=2,
        command_count=command_count,
        initial_thread_conversations=_destinations(initial),
        shared_thread_conversations=_destinations(shared),
        switched_old_thread_conversations=_destinations(switched_old),
        switched_new_thread_conversations=_destinations(switched_new),
        switched_back_conversations=_destinations(switched_back),
        worker_max_active=(
            application.max_active_observation_workers(first_thread_ref),
            application.max_active_observation_workers(second_thread_ref),
        ),
        worker_subscription_calls=(
            application.subscription_calls(first_thread_ref),
            application.subscription_calls(second_thread_ref),
        ),
        recovered_conversations=_destinations(recovered),
        recovered_delivery_count=len(recovered),
        reconstructed_bindings=reconstructed_bindings,
        reconstructed_receipts=reconstructed_receipts,
        recovery_redispatched_input=recovery_redispatched_input,
        sqlite_files_inspected=sqlite_files_inspected,
        sqlite_table_count=sqlite_table_count,
        sqlite_leak_free=sqlite_leak_free,
        diagnostics_schema_version=diagnostics_schema_version,
        diagnostics_size=diagnostics_size,
        diagnostics_authoritative=diagnostics_authoritative,
        adapters_stopped=(
            not gateway.running
            and not consumer.channel.started
            and not restarted.gateway.running
            and not restarted.channel.started
            and not application.started
        ),
        active_workers_after_shutdown=active_workers,
        registry_active_after_shutdown=registry_active,
        owned_tasks_after_shutdown=len(owned_tasks),
    )


def main() -> None:
    with TemporaryDirectory(prefix="imagent-reference-") as reference_workspace:
        report = asyncio.run(run_reference_consumer(reference_workspace))
    if not (
        report.adapters_stopped
        and report.active_workers_after_shutdown == 0
        and report.registry_active_after_shutdown == 0
        and report.owned_tasks_after_shutdown == 0
    ):
        raise RuntimeError("reference consumer shutdown did not drain owned resources")
    print(
        "reference consumer OK: "
        f"projects={report.project_count} "
        f"threads={report.thread_count} "
        f"conversations={report.conversation_count} "
        f"max_workers={max(report.worker_max_active)} "
        "diagnostics=bounded sqlite_recovery=true shutdown=true"
    )


if __name__ == "__main__":
    main()


__all__ = ["ReferenceReport", "main", "run_reference_consumer"]

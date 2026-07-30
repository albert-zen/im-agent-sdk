from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

from imagent.contracts import (
    AcceptedTurn,
    AgentEvent,
    AgentInput,
    ApplicationCapabilities,
    ApplicationRef,
    ApplicationSummary,
    ChannelCapabilities,
    ChannelMessage,
    DeliveryReceipt,
    Page,
    ProjectCapabilities,
    ProjectMode,
    ProjectRef,
    ProjectSummary,
    RuntimeCapabilities,
    SupportLevel,
    ThreadCapabilities,
    ThreadDeletionCapability,
    ThreadRef,
    ThreadSnapshot,
    ThreadStatus,
    ThreadSummary,
)


def make_capabilities(project_mode: ProjectMode) -> ApplicationCapabilities:
    project_support = (
        SupportLevel.NATIVE if project_mode is ProjectMode.MANAGED else SupportLevel.UNSUPPORTED
    )
    return ApplicationCapabilities(
        projects=ProjectCapabilities(
            mode=project_mode,
            discovery=project_support,
            selection=project_support,
        ),
        threads=ThreadCapabilities(
            listing=SupportLevel.NATIVE,
            creation=SupportLevel.NATIVE,
            switching=SupportLevel.NATIVE,
            deletion=ThreadDeletionCapability.PERMANENT,
        ),
        runtime=RuntimeCapabilities(
            history=SupportLevel.NATIVE,
            streaming=SupportLevel.NATIVE,
            replay_from_cursor=SupportLevel.NATIVE,
            interruption=SupportLevel.NATIVE,
            interactive_requests=SupportLevel.NATIVE,
        ),
    )


class FakeChannelAdapter:
    def __init__(self, channel_instance_id: str = "fake-channel") -> None:
        self._channel_instance_id = channel_instance_id
        self._capabilities = ChannelCapabilities()
        self.started = False
        self.sent: list[ChannelMessage] = []

    @property
    def channel_instance_id(self) -> str:
        return self._channel_instance_id

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    async def start(self, on_message, on_operation) -> None:
        self.started = True
        self.on_message = on_message
        self.on_operation = on_operation

    async def stop(self) -> None:
        self.started = False

    async def send(self, message: ChannelMessage) -> DeliveryReceipt:
        self.sent.append(message)
        return DeliveryReceipt(
            status="accepted_by_platform",
            native_message_id=f"sent-{len(self.sent)}",
        )


class FakeAgentApplicationAdapter:
    def __init__(
        self,
        application_instance_id: str = "fake-agent",
        project_mode: ProjectMode = ProjectMode.MANAGED,
    ) -> None:
        self._application_id = application_instance_id
        self._capabilities = make_capabilities(project_mode)
        self._summary = ApplicationSummary(
            ref=ApplicationRef(application_instance_id),
            kind="fake",
            display_name="Fake Agent",
            capabilities=self._capabilities,
        )
        self._projects: dict[ProjectRef, ProjectSummary] = {}
        if project_mode is ProjectMode.MANAGED:
            ref = ProjectRef(application_instance_id, "contract-project")
            self._projects[ref] = ProjectSummary(ref=ref, display_name="Contract Project")
        self._threads: dict[ThreadRef, ThreadSummary] = {}
        self._inputs: list[tuple[ThreadRef, AgentInput]] = []
        self._events: dict[ThreadRef, asyncio.Queue[AgentEvent]] = {}
        self._next_thread = 1

    @property
    def summary(self) -> ApplicationSummary:
        return self._summary

    @property
    def capabilities(self) -> ApplicationCapabilities:
        return self._capabilities

    async def list_projects(self, cursor=None) -> Page[ProjectSummary]:
        return Page(tuple(self._projects.values()))

    async def get_project(self, project_ref: ProjectRef) -> ProjectSummary:
        return self._projects[project_ref]

    async def list_threads(self, project_ref=None, cursor=None) -> Page[ThreadSummary]:
        return Page(
            tuple(
                thread
                for thread in self._threads.values()
                if project_ref is None or thread.ref.project_ref == project_ref
            )
        )

    async def create_thread(self, project_ref=None, title=None) -> ThreadSummary:
        ref = ThreadRef(
            application_instance_id=self._application_id,
            native_thread_id=f"thread-{self._next_thread}",
            project_ref=project_ref,
        )
        self._next_thread += 1
        summary = ThreadSummary(
            ref=ref,
            status=ThreadStatus.IDLE,
            title=title,
            updated_at=datetime.now(UTC),
        )
        self._threads[ref] = summary
        self._events[ref] = asyncio.Queue()
        return summary

    async def get_thread(self, thread_ref: ThreadRef) -> ThreadSummary:
        return self._threads[thread_ref]

    async def delete_thread(self, thread_ref: ThreadRef) -> None:
        del self._threads[thread_ref]
        self._events.pop(thread_ref, None)

    async def read_thread(self, thread_ref: ThreadRef, cursor=None) -> ThreadSnapshot:
        return ThreadSnapshot(thread=self._threads[thread_ref], messages=(), cursor=cursor)

    async def send_input(self, thread_ref: ThreadRef, message: AgentInput) -> AcceptedTurn:
        self._inputs.append((thread_ref, message))
        self._threads[thread_ref] = replace(
            self._threads[thread_ref],
            status=ThreadStatus.RUNNING,
            updated_at=datetime.now(UTC),
        )
        return AcceptedTurn(
            thread_ref=thread_ref,
            turn_id=f"turn-{len(self._inputs)}",
            client_message_id=message.client_message_id,
        )

    async def subscribe_thread(
        self,
        thread_ref: ThreadRef,
        after_cursor=None,
    ) -> AsyncIterator[AgentEvent]:
        queue = self._events[thread_ref]
        while True:
            yield await queue.get()

    async def get_thread_status(self, thread_ref: ThreadRef) -> ThreadStatus:
        return self._threads[thread_ref].status

    async def interrupt_turn(self, thread_ref: ThreadRef, turn_id=None) -> None:
        self._threads[thread_ref] = replace(
            self._threads[thread_ref],
            status=ThreadStatus.INTERRUPTED,
        )

    async def respond_request(self, request_id: str, response: object) -> None:
        return None

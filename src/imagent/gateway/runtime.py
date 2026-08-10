"""Canonical public v1 Gateway composition and owned lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import uuid4

from ..applications.contract import AgentApplicationAdapter, ApplicationRef
from ..interaction.channels.contract import ChannelAdapter
from ..interaction.controllers.contract import ControllerLifecycle, InboundController
from ..interaction.messages import ConversationRef
from ..interaction.operations import require_identifier
from . import ImAgentGateway
from .actions import ApplicationActions, ConversationActions
from .composition import GatewayExtensions, GatewayLimits, GatewayRepositories
from .diagnostics import DiagnosticsSnapshot
from .persistence.store import GatewayStore, GatewayStoreSession
from .routing.projection_routes import ProjectionPolicy

_LEASE_DURATION_SECONDS = 30.0
_LEASE_RENEWAL_SECONDS = 10.0


class Gateway:
    """One explicit v1 object graph over one coherent GatewayStore namespace."""

    def __init__(
        self,
        *,
        gateway_id: str,
        channels: list[ChannelAdapter],
        applications: list[AgentApplicationAdapter],
        store: GatewayStore,
        controller: InboundController | None = None,
        projection_policy: ProjectionPolicy = ProjectionPolicy.REMEMBERED_LAST_RECIPIENT,
        limits: GatewayLimits = GatewayLimits(),
        extensions: GatewayExtensions = GatewayExtensions(),
    ) -> None:
        require_identifier(gateway_id, "gateway_id")
        if not channels:
            raise ValueError("Gateway requires at least one Channel")
        if not applications:
            raise ValueError("Gateway requires at least one Agent Application")
        channel_ids = tuple(channel.channel_instance_id for channel in channels)
        application_ids = tuple(
            application.summary.ref.application_instance_id for application in applications
        )
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("Gateway Channel instance IDs must be unique")
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("Gateway Application instance IDs must be unique")
        if controller is not None and extensions.controller is not None:
            raise ValueError("configure the Controller once through Gateway.controller")
        if not isinstance(projection_policy, ProjectionPolicy):
            raise ValueError("projection_policy must be a ProjectionPolicy")

        self._gateway_id = gateway_id
        self._channels = list(channels)
        self._applications = list(applications)
        self._store = store
        self._controller = controller if controller is not None else extensions.controller
        self._projection_policy = projection_policy
        self._limits = limits
        self._extensions = replace(extensions, controller=self._controller)
        self._session: GatewayStoreSession | None = None
        self._runtime: ImAgentGateway | None = None
        self._lease_stop: asyncio.Event | None = None
        self._lease_task: asyncio.Task[None] | None = None
        self._startup_task: asyncio.Task[object] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._lease_failure: BaseException | None = None
        self._terminal_error: BaseException | None = None
        self._runtime_started = False
        self._started = False
        self._closed = False

    @property
    def running(self) -> bool:
        return self._started

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.stop()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("Gateway cannot restart after its owned store is closed")
            if isinstance(self._controller, ControllerLifecycle):
                self._controller.validate_startup()
            startup_task = asyncio.current_task()
            if startup_task is None:
                raise RuntimeError("Gateway startup requires an asyncio Task owner")
            self._startup_task = startup_task
            try:
                session = await self._store.acquire_runtime(
                    gateway_id=self._gateway_id,
                    owner_token=uuid4().hex,
                    lease_duration_seconds=_LEASE_DURATION_SECONDS,
                )
                self._session = session
                self._lease_stop = asyncio.Event()
                self._lease_task = asyncio.create_task(
                    self._supervise_lease(session, self._lease_stop),
                    name="imagent-gateway-lease-renewal",
                )
                identities = tuple(
                    identity
                    for application in self._applications
                    if (identity := application.summary.workspace_identity) is not None
                )
                runtime = ImAgentGateway(
                    channels=self._channels,
                    applications=self._applications,
                    repositories=GatewayRepositories(bindings=session),
                    limits=self._limits,
                    extensions=self._extensions,
                    projection_policy=self._projection_policy,
                )
                self._runtime = runtime
                await session.check_workspace_identities(identities)
                await runtime.start()
                self._runtime_started = True
                if self._lease_failure is not None:
                    raise self._lease_failure
            except BaseException as error:
                primary = self._lease_failure or error
                await self._close_owned_resources(primary, from_lease_task=False)
                if self._lease_failure is not None and error is not self._lease_failure:
                    raise RuntimeError("Gateway lease renewal failed during startup") from (
                        self._lease_failure
                    )
                raise
            self._started = True
            self._startup_task = None

    async def stop(self) -> None:
        startup_task = self._startup_task
        current = asyncio.current_task()
        if startup_task is not None and startup_task is not current and not startup_task.done():
            startup_task.cancel()
        async with self._lifecycle_lock:
            if self._closed:
                return
            error = await self._close_owned_resources(None, from_lease_task=False)
            if error is not None:
                raise error

    async def wait_closed(self) -> None:
        """Wait for explicit stop or lease-loss shutdown and surface runtime failure."""
        await self._closed_event.wait()
        if self._terminal_error is not None:
            raise RuntimeError("Gateway closed after a runtime failure") from self._terminal_error

    def actions(
        self,
        conversation_ref: ConversationRef,
        *,
        actor: str,
    ) -> ConversationActions:
        return self._require_runtime()._conversation_actions(
            conversation_ref,
            actor=actor,
        )

    def application(
        self,
        application_ref: ApplicationRef,
        *,
        principal: str,
    ) -> ApplicationActions:
        return self._require_runtime()._application_actions(
            application_ref,
            principal=principal,
        )

    def diagnostics(self) -> DiagnosticsSnapshot:
        return self._require_runtime().diagnostics_snapshot()

    def _require_runtime(self) -> ImAgentGateway:
        if not self._started or self._runtime is None:
            raise RuntimeError("Gateway actions require a running Gateway")
        return self._runtime

    async def _supervise_lease(
        self,
        session: GatewayStoreSession,
        stop: asyncio.Event,
    ) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_LEASE_RENEWAL_SECONDS)
                except TimeoutError:
                    await session.renew(lease_duration_seconds=_LEASE_DURATION_SECONDS)
                    continue
                return
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._lease_failure = error
            if self._started:
                await self._close_owned_resources(error, from_lease_task=True)
                return
            startup_task = self._startup_task
            if startup_task is not None and not startup_task.done():
                startup_task.cancel()

    async def _close_owned_resources(
        self,
        primary: BaseException | None,
        *,
        from_lease_task: bool,
    ) -> BaseException | None:
        async with self._shutdown_lock:
            if self._closed:
                return primary
            error = primary
            self._started = False
            runtime = self._runtime
            if runtime is not None:
                runtime._deactivate_scoped_actions()
            if runtime is not None and self._runtime_started:
                try:
                    await runtime.stop()
                except BaseException as runtime_error:
                    error = _append_cleanup_error(error, "Gateway runtime", runtime_error)
            self._runtime_started = False
            lease_stop = self._lease_stop
            if lease_stop is not None:
                lease_stop.set()
            lease_task = self._lease_task
            current = asyncio.current_task()
            if lease_task is not None and lease_task is not current:
                if not lease_task.done():
                    lease_task.cancel()
                try:
                    await lease_task
                except asyncio.CancelledError:
                    pass
                except BaseException as lease_error:
                    error = _append_cleanup_error(error, "Gateway lease renewal", lease_error)
            session = self._session
            if session is not None:
                try:
                    await session.close()
                except BaseException as session_error:
                    error = _append_cleanup_error(error, "Gateway session", session_error)
            try:
                await self._store.close()
            except BaseException as store_error:
                error = _append_cleanup_error(error, "Gateway store", store_error)
            self._runtime = None
            self._session = None
            self._lease_stop = None
            self._lease_task = None
            self._startup_task = None
            self._closed = True
            self._terminal_error = error
            self._closed_event.set()
            if from_lease_task:
                return None
            return error


def _append_cleanup_error(
    primary: BaseException | None,
    owner: str,
    cleanup_error: BaseException,
) -> BaseException:
    if primary is None:
        return cleanup_error
    primary.add_note(f"{owner} cleanup also failed: {cleanup_error!r}")
    return primary


__all__ = ["Gateway"]

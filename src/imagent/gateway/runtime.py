"""Canonical public v1 Gateway composition and owned lifecycle."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from types import TracebackType
from typing import Self, cast
from uuid import uuid4

from ..applications.contract import (
    AgentApplicationAdapter,
    ApplicationRef,
    ApplicationSummary,
    validate_application_summary,
)
from ..interaction.channels.contract import ChannelAdapter, ChannelCapabilities
from ..interaction.controllers.contract import ControllerLifecycle, InboundController
from ..interaction.messages import ConversationRef
from ..interaction.operations import require_identifier
from . import ImAgentGateway
from .actions import ApplicationActions, ConversationActions
from .composition import GatewayExtensions, GatewayLimits, GatewayRepositories
from .diagnostics import DiagnosticsSnapshot, _bounded_cleanup_error_summary
from .lifecycle import _public_lifecycle_error
from .persistence.store import (
    GatewayStore,
    GatewayStoreSession,
    _is_gateway_store_session,
    validate_runtime_lease,
)
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
        if not isinstance(limits, GatewayLimits):
            raise TypeError("limits must be GatewayLimits")
        if not isinstance(extensions, GatewayExtensions):
            raise TypeError("extensions must be GatewayExtensions")
        if not channels:
            raise ValueError("Gateway requires at least one Channel")
        if not applications:
            raise ValueError("Gateway requires at least one Agent Application")
        channel_ids = _validate_channels(channels)
        application_summaries = _validate_applications(applications)
        application_ids = tuple(
            summary.ref.application_instance_id for summary in application_summaries
        )
        _validate_store(store)
        if controller is not None and extensions.controller is not None:
            raise ValueError("configure the Controller once through Gateway.controller")
        if not isinstance(projection_policy, ProjectionPolicy):
            raise ValueError("projection_policy must be a ProjectionPolicy")

        self._gateway_id = gateway_id
        self._channels = list(channels)
        self._channel_ids = channel_ids
        self._channel_snapshot = _channel_composition_snapshot(self._channels)
        self._applications = list(applications)
        self._application_ids = application_ids
        self._application_snapshot = _application_composition_snapshot(self._applications)
        self._store = store
        self._store_snapshot = _store_composition_snapshot(self._store)
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
            self._validate_stable_composition()
            if isinstance(self._controller, ControllerLifecycle):
                self._controller.validate_startup()
            startup_task = asyncio.current_task()
            if startup_task is None:
                raise RuntimeError("Gateway startup requires an asyncio Task owner")
            self._startup_task = startup_task
            try:
                owner_token = uuid4().hex
                candidate = await self._store.acquire_runtime(
                    gateway_id=self._gateway_id,
                    owner_token=owner_token,
                    lease_duration_seconds=_LEASE_DURATION_SECONDS,
                )
                try:
                    session = _validate_acquired_session(
                        candidate,
                        gateway_id=self._gateway_id,
                        owner_token=owner_token,
                    )
                    self._validate_stable_composition()
                except BaseException as validation_error:
                    await _close_invalid_session_candidate(candidate, validation_error)
                    raise
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

    def _validate_stable_composition(self) -> None:
        channel_ids = _validate_channels(self._channels)
        if channel_ids != self._channel_ids:
            raise ValueError("Gateway Channel instance identity changed after composition")
        if _channel_composition_snapshot(self._channels) != self._channel_snapshot:
            raise ValueError("Gateway Channel composition changed after composition")
        summaries = _validate_applications(self._applications)
        application_ids = tuple(summary.ref.application_instance_id for summary in summaries)
        if application_ids != self._application_ids:
            raise ValueError("Gateway Application instance identity changed after composition")
        if _application_composition_snapshot(self._applications) != self._application_snapshot:
            raise ValueError("Gateway Application composition changed after composition")
        _validate_store(self._store)
        if _store_composition_snapshot(self._store) != self._store_snapshot:
            raise ValueError("GatewayStore composition changed after composition")

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
        primary = _public_lifecycle_error(cleanup_error, owner)
        primary.add_note(
            f"Gateway cleanup failed: {_bounded_cleanup_error_summary(owner, cleanup_error)}"
        )
        return primary
    primary = _public_lifecycle_error(primary, "Gateway lifecycle")
    primary.add_note(
        f"Gateway cleanup also failed: {_bounded_cleanup_error_summary(owner, cleanup_error)}"
    )
    return primary


def _freeze_composition_value(value: object) -> object:
    """Capture validated composition values without retaining mutable mappings."""

    if isinstance(value, Enum):
        return ("enum", type(value), value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            type(value),
            tuple(
                (field.name, _freeze_composition_value(getattr(value, field.name)))
                for field in fields(value)
            ),
        )
    if isinstance(value, Mapping):
        entries = tuple(
            (_freeze_composition_value(key), _freeze_composition_value(item))
            for key, item in value.items()
        )
        return ("mapping", tuple(sorted(entries, key=repr)))
    if isinstance(value, (tuple, list)):
        return ("sequence", tuple(_freeze_composition_value(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (
            "set",
            tuple(sorted((_freeze_composition_value(item) for item in value), key=repr)),
        )
    if isinstance(value, float) and value != value:
        return ("scalar", type(value), "nan")
    if isinstance(value, (str, int, float, bool, bytes, type(None))):
        return ("scalar", type(value), value)
    try:
        representation = repr(value)
    except BaseException:
        representation = "<unrepresentable>"
    return ("object", type(value), representation[:512])


def _callable_composition_shape(value: object) -> tuple[object, ...]:
    target = getattr(value, "__func__", value)
    return (
        type(target),
        getattr(target, "__module__", None),
        getattr(target, "__qualname__", None),
        inspect.iscoroutinefunction(target),
    )


def _channel_composition_snapshot(
    channels: list[ChannelAdapter],
) -> tuple[object, ...]:
    return tuple(
        (
            type(channel),
            channel.channel_instance_id,
            _freeze_composition_value(channel.capabilities),
            tuple(
                (name, _callable_composition_shape(getattr(channel, name)))
                for name in ("start", "stop", "send")
            ),
        )
        for channel in channels
    )


def _application_composition_snapshot(
    applications: list[AgentApplicationAdapter],
) -> tuple[object, ...]:
    return tuple(
        (
            type(application),
            _freeze_composition_value(application.summary),
            tuple(
                (name, _callable_composition_shape(getattr(application, name)))
                for name in (
                    "start",
                    "stop",
                    "execute",
                    "send_input",
                    "list_pending_requests",
                    "subscribe_thread",
                )
            ),
        )
        for application in applications
    )


def _store_composition_snapshot(store: GatewayStore) -> tuple[object, ...]:
    return (
        type(store),
        _freeze_composition_value(store.max_effect_receipts),
        tuple(
            (name, _callable_composition_shape(getattr(store, name)))
            for name in ("acquire_runtime", "close")
        ),
    )


def _validate_channels(channels: list[ChannelAdapter]) -> tuple[str, ...]:
    channel_ids: list[str] = []
    for channel in channels:
        channel_id = getattr(channel, "channel_instance_id", None)
        if not isinstance(channel_id, str):
            raise TypeError("Gateway Channel instance ID must be a string")
        require_identifier(channel_id, "channel_instance_id")
        if not isinstance(getattr(channel, "capabilities", None), ChannelCapabilities):
            raise TypeError("Gateway Channel capabilities must be ChannelCapabilities")
        for method_name in ("start", "stop", "send"):
            _require_async_callable(
                getattr(channel, method_name, None),
                f"Gateway Channel {method_name}()",
            )
        channel_ids.append(channel_id)
    if len(channel_ids) != len(set(channel_ids)):
        raise ValueError("Gateway Channel instance IDs must be unique")
    return tuple(channel_ids)


def _validate_applications(
    applications: list[AgentApplicationAdapter],
) -> tuple[ApplicationSummary, ...]:
    summaries: list[ApplicationSummary] = []
    for application in applications:
        summary = getattr(application, "summary", None)
        if not isinstance(summary, ApplicationSummary):
            raise TypeError("Gateway Application summary must be ApplicationSummary")
        validate_application_summary(summary)
        for method_name in (
            "start",
            "stop",
            "execute",
            "send_input",
            "list_pending_requests",
        ):
            _require_async_callable(
                getattr(application, method_name, None),
                f"Gateway Application {method_name}()",
            )
        if not callable(getattr(application, "subscribe_thread", None)):
            raise TypeError("Gateway Application must define callable subscribe_thread()")
        summaries.append(summary)
    application_ids = tuple(summary.ref.application_instance_id for summary in summaries)
    if len(application_ids) != len(set(application_ids)):
        raise ValueError("Gateway Application instance IDs must be unique")
    return tuple(summaries)


def _validate_store(store: GatewayStore) -> None:
    max_effect_receipts = getattr(store, "max_effect_receipts", None)
    if (
        not isinstance(max_effect_receipts, int)
        or isinstance(max_effect_receipts, bool)
        or max_effect_receipts < 1
    ):
        raise TypeError("GatewayStore max_effect_receipts must be a positive integer")
    for method_name in ("acquire_runtime", "close"):
        _require_async_callable(
            getattr(store, method_name, None),
            f"GatewayStore {method_name}()",
        )


def _require_async_callable(value: object, description: str) -> None:
    if not callable(value):
        raise TypeError(f"{description} must be an async callable")
    if inspect.iscoroutinefunction(value):
        return
    call = getattr(value, "__call__", None)
    if inspect.iscoroutinefunction(call):
        return
    raise TypeError(f"{description} must be an async callable")


def _validate_acquired_session(
    candidate: object,
    *,
    gateway_id: str,
    owner_token: str,
) -> GatewayStoreSession:
    if not _is_gateway_store_session(candidate):
        raise TypeError("GatewayStore.acquire_runtime() must return GatewayStoreSession")
    validate_runtime_lease(candidate.lease)
    if candidate.lease.gateway_id != gateway_id:
        raise ValueError("GatewayStoreSession lease belongs to another Gateway")
    if candidate.lease.owner_token != owner_token:
        raise ValueError("GatewayStoreSession lease owner token does not match acquisition")
    return candidate


async def _close_invalid_session_candidate(
    candidate: object,
    primary: BaseException,
) -> None:
    close = getattr(candidate, "close", None)
    if not callable(close):
        primary.add_note("Malformed GatewayStoreSession exposed no callable close()")
        return
    try:
        await cast(Callable[[], Awaitable[None]], close)()
    except BaseException as cleanup_error:
        primary.add_note(
            "Gateway cleanup also failed: "
            + _bounded_cleanup_error_summary(
                "Malformed GatewayStoreSession",
                cleanup_error,
            )
        )


__all__ = ["Gateway"]

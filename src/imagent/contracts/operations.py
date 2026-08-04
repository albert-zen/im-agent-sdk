"""Pending Gateway projection and request-correlation operation values.

The shared Gateway discriminator/base and aggregate unions live in
``imagent.gateway.routing.operations``. These exact values remain here until
the later projection-route and request-correlation slices move their owners;
this module no longer assembles or validates the aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ForwardRef

from ..applications.contract import ThreadRef
from ..applications.requests import RequestRef as _RequestRef
from ..applications.requests import RequestResponse as _RequestResponse
from ..gateway.routing.operations import (
    GatewayOperationType as _GatewayOperationType,
)
from ..gateway.routing.operations import (
    _GatewayOperation,
    _GatewayOperationSucceeded,
)

if TYPE_CHECKING:
    from ..gateway.persistence.state_contracts import ThreadProjectionRoute


@dataclass(frozen=True, slots=True, kw_only=True)
class ObserveThread(_GatewayOperation):
    thread_ref: ThreadRef
    reply_to_message_id: str | None = None
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.THREAD_OBSERVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RespondToRequest(_GatewayOperation):
    request_ref: _RequestRef
    response: _RequestResponse
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadObserved(_GatewayOperationSucceeded):
    route: ThreadProjectionRoute
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.THREAD_OBSERVE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestResponseRouted(_GatewayOperationSucceeded):
    request_ref: _RequestRef
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


ThreadObserved.__annotations__["route"] = ForwardRef(
    "ThreadProjectionRoute",
    module="imagent.gateway.persistence.state_contracts",
)


from ..gateway.routing.operations import _complete_gateway_union  # noqa: E402

_complete_gateway_union()

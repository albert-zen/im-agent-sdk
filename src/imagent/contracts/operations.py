"""Pending Gateway request-correlation operation values.

The shared Gateway discriminator/base and aggregate unions live in
``imagent.gateway.routing.operations``. These exact request values remain here
until the later request-correlation slice moves their owner; this module no
longer assembles or validates the aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..applications.requests import RequestRef as _RequestRef
from ..applications.requests import RequestResponse as _RequestResponse
from ..gateway.routing.operations import (
    GatewayOperationType as _GatewayOperationType,
)
from ..gateway.routing.operations import (
    _GatewayOperation,
    _GatewayOperationSucceeded,
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
class RequestResponseRouted(_GatewayOperationSucceeded):
    request_ref: _RequestRef
    type: _GatewayOperationType = field(
        init=False,
        default=_GatewayOperationType.CONVERSATION_RESPOND_REQUEST,
    )


from ..gateway.routing.operations import _complete_gateway_union  # noqa: E402

_complete_gateway_union()

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime

from ...applications.contract import ThreadRef
from ...applications.requests import RequestRef
from ...interaction.messages import ConversationRef
from ..persistence.repository_contracts import RequestCorrelationConflict
from ..persistence.state_contracts import (
    RequestRouteCorrelation,
    RequestRouteState,
    validate_request_route_correlation,
)


def derive_request_correlation_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-route:sha256:{digest}"


def derive_request_delivery_id(
    request_ref: RequestRef,
    conversation_ref: ConversationRef,
) -> str:
    identity = json.dumps(
        [
            request_ref.application_ref.application_instance_id,
            request_ref.native_request_id,
            conversation_ref.channel_instance_id,
            conversation_ref.native_conversation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"imagent:request-delivery:sha256:{digest}"


def _merge_correlation(
    existing: RequestRouteCorrelation | None,
    replacement: RequestRouteCorrelation,
    *,
    request_correlations: tuple[RequestRouteCorrelation, ...],
) -> RequestRouteCorrelation:
    if existing is None:
        terminal = max(
            (
                correlation
                for correlation in request_correlations
                if correlation.state is not RequestRouteState.OPEN
            ),
            key=lambda correlation: (
                _REQUEST_STATE_PRECEDENCE[correlation.state],
                correlation.updated_at,
            ),
            default=None,
        )
        if terminal is None:
            return replacement
        return replace(
            replacement,
            state=terminal.state,
            updated_at=max(replacement.updated_at, terminal.updated_at),
        )
    if (
        existing.correlation_id != replacement.correlation_id
        or existing.request_ref != replacement.request_ref
        or existing.thread_ref != replacement.thread_ref
        or existing.turn_id != replacement.turn_id
        or existing.conversation_ref != replacement.conversation_ref
        or existing.response_shape != replacement.response_shape
    ):
        raise RequestCorrelationConflict(
            f"request correlation identity changed: {replacement.correlation_id}"
        )
    if existing.state in {
        RequestRouteState.RESPONDED,
        RequestRouteState.RESOLVED,
        RequestRouteState.STALE,
    }:
        return existing
    return replacement


_REQUEST_STATE_PRECEDENCE = {
    RequestRouteState.OPEN: 0,
    RequestRouteState.RESPONDED: 1,
    RequestRouteState.STALE: 2,
    RequestRouteState.RESOLVED: 3,
}


def _transition_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    expected_states: tuple[RequestRouteState, ...],
    state: RequestRouteState,
    updated_at: datetime,
) -> tuple[RequestRouteCorrelation, ...]:
    if not correlations:
        raise KeyError("request correlation does not exist")
    if all(correlation.state is state for correlation in correlations):
        return correlations
    expected = set(expected_states)
    if any(correlation.state not in expected for correlation in correlations):
        raise RequestCorrelationConflict("request correlation state changed")
    target_precedence = _REQUEST_STATE_PRECEDENCE[state]
    if any(
        target_precedence < _REQUEST_STATE_PRECEDENCE[correlation.state]
        for correlation in correlations
    ):
        raise RequestCorrelationConflict("request correlation state cannot regress")
    transitioned = tuple(
        replace(correlation, state=state, updated_at=updated_at) for correlation in correlations
    )
    for correlation in transitioned:
        validate_request_route_correlation(correlation)
    return transitioned


def _reject_conflicting_endpoint(
    correlations,
    replacement: RequestRouteCorrelation,
) -> None:
    for existing in correlations:
        if (
            existing.request_ref == replacement.request_ref
            and existing.conversation_ref == replacement.conversation_ref
            and existing.correlation_id != replacement.correlation_id
        ):
            raise RequestCorrelationConflict(
                "request destination belongs to a different correlation"
            )


def _select_correlations(
    correlations: tuple[RequestRouteCorrelation, ...],
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
) -> tuple[RequestRouteCorrelation, ...]:
    return tuple(
        correlation
        for correlation in correlations
        if _matches(
            correlation,
            request_ref=request_ref,
            thread_ref=thread_ref,
            conversation_ref=conversation_ref,
            older_than=None,
        )
    )


def _matches(
    correlation: RequestRouteCorrelation,
    *,
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> bool:
    return (
        (request_ref is None or correlation.request_ref == request_ref)
        and (thread_ref is None or correlation.thread_ref == thread_ref)
        and (conversation_ref is None or correlation.conversation_ref == conversation_ref)
        and (older_than is None or correlation.updated_at < older_than)
    )


def _require_delete_selector(
    request_ref: RequestRef | None,
    thread_ref: ThreadRef | None,
    conversation_ref: ConversationRef | None,
    older_than: datetime | None,
) -> None:
    if (
        request_ref is None
        and thread_ref is None
        and conversation_ref is None
        and older_than is None
    ):
        raise ValueError("request correlation deletion requires at least one selector")

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .adapters import IdempotencyRepository
from .contracts import ApplicationInputOutcomeUnknown, OutboundMessage
from .controllers import InboundFailurePhase, InboundFailurePresenter
from .controllers.failures import present_inbound_failure
from .inbound_admission import ClaimedInbound
from .projection_runtime import InputPostAcceptanceError


async def handle_claimed_inbound(
    claimed: ClaimedInbound,
    *,
    process: Callable[[], Awaitable[None]],
    idempotency: IdempotencyRepository,
    presenter: InboundFailurePresenter | None,
    deliver: Callable[[OutboundMessage], Awaitable[object]],
) -> None:
    try:
        await process()
    except InputPostAcceptanceError as exc:
        try:
            await idempotency.complete(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
        except BaseException as terminal_error:
            raise terminal_error from exc.cause
        if presenter is None:
            raise exc.cause.with_traceback(exc.cause.__traceback__) from None
        await present_inbound_failure(
            presenter,
            claimed.message,
            exc,
            InboundFailurePhase.POST_ACCEPTANCE,
            deliver,
        )
    except ApplicationInputOutcomeUnknown as exc:
        if presenter is None:
            raise exc.cause.with_traceback(exc.cause.__traceback__) from None
        await present_inbound_failure(
            presenter,
            claimed.message,
            exc,
            InboundFailurePhase.OUTCOME_UNKNOWN,
            deliver,
        )
    except BaseException as exc:
        if presenter is None or not isinstance(exc, Exception):
            await idempotency.release(
                claimed.scope,
                claimed.key,
                owner_token=claimed.owner_token,
            )
            raise
        # Complete before the Channel side effect. If presentation is rejected
        # or ambiguous, redelivery cannot become a later Application mutation.
        await idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
        )
        await present_inbound_failure(
            presenter,
            claimed.message,
            exc,
            InboundFailurePhase.PRE_ACCEPTANCE,
            deliver,
        )
    else:
        await idempotency.complete(
            claimed.scope,
            claimed.key,
            owner_token=claimed.owner_token,
        )

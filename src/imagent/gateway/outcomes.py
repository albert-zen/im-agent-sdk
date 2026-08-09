"""Closed v1 runtime outcome algebra."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

T = TypeVar("T")
E = TypeVar("E")


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class Succeeded(Generic[T]):
    value: T
    status: OutcomeStatus = field(init=False, default=OutcomeStatus.SUCCEEDED)


@dataclass(frozen=True, slots=True)
class Failed(Generic[E]):
    error: E
    status: OutcomeStatus = field(init=False, default=OutcomeStatus.FAILED)


@dataclass(frozen=True, slots=True)
class Partial(Generic[T, E]):
    value: T
    error: E
    status: OutcomeStatus = field(init=False, default=OutcomeStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class OutcomeUnknown(Generic[E]):
    error: E
    status: OutcomeStatus = field(init=False, default=OutcomeStatus.OUTCOME_UNKNOWN)


Outcome: TypeAlias = Succeeded[T] | Failed[E] | Partial[T, E] | OutcomeUnknown[E]


__all__ = [
    "Failed",
    "Outcome",
    "OutcomeStatus",
    "OutcomeUnknown",
    "Partial",
    "Succeeded",
]

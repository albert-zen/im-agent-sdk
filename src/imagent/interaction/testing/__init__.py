"""Bounded adapter contract checks and representative conformance fakes."""

from .contracts import (
    ContractCheck,
    ContractReport,
    sample_conversation,
    verify_application_adapter,
    verify_channel_adapter,
)
from .fakes import FakeAgentApplicationAdapter, FakeChannelAdapter, make_capabilities

__all__ = [
    "ContractCheck",
    "ContractReport",
    "FakeAgentApplicationAdapter",
    "FakeChannelAdapter",
    "make_capabilities",
    "sample_conversation",
    "verify_application_adapter",
    "verify_channel_adapter",
]

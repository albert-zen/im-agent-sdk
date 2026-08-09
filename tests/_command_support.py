from __future__ import annotations

from imagent import CommandRegistry, include_common_commands


def common_command_registry() -> CommandRegistry:
    registry = CommandRegistry()
    include_common_commands(registry)
    registry.freeze()
    return registry

"""Typed conflict outcomes shared by Gateway repository implementations."""


class BindingConflict(RuntimeError):
    """An expected Conversation binding revision did not match."""

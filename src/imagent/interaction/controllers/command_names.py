"""Pure lookup over a consumer's complete, normalized command namespace."""

from collections.abc import Mapping


def resolve_command_name(
    name: str,
    names: Mapping[str, str],
    *,
    allow_unique_prefix: bool = False,
) -> tuple[str, ...]:
    """Return canonical candidates, giving an exact name or alias precedence.

    Callers own parsing and provide a trusted, bounded normalized name-to-
    canonical table covering all dispatch branches. The table must not change
    during lookup. No argument, registration, authorization, or execution state
    is inspected or changed. Empty input never expands to every command.
    """
    if type(allow_unique_prefix) is not bool:
        raise ValueError("allow_unique_prefix must be a Boolean")
    if not name:
        return ()
    exact = names.get(name)
    if exact is not None:
        return (exact,)
    if not allow_unique_prefix:
        return ()
    return tuple(sorted({canonical for key, canonical in names.items() if key.startswith(name)}))

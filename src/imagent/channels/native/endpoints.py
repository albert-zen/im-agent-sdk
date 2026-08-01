from __future__ import annotations

from urllib.parse import urlsplit


def validate_http_endpoint(value: str, *, key: str) -> None:
    """Validate a non-empty HTTP(S) base URL without embedded credentials."""

    if any(character.isspace() for character in value):
        raise ValueError(f"{key} must not contain whitespace")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{key} must be a valid HTTP(S) URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not host:
        raise ValueError(f"{key} must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{key} must not contain userinfo credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{key} must not contain query or fragment credentials")

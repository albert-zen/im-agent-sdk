"""Private immutable App Server creation configuration; no native dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType

from ...capabilities import (
    MAX_THREAD_CREATION_PROFILES,
    ThreadCreationProfile,
    validate_thread_creation_profile,
)


def _thread_start_options(
    options: Mapping[str, object] | None,
) -> Mapping[str, object]:
    copied = deepcopy(dict(options or {}))
    invalid_keys = [key for key in copied if not isinstance(key, str) or not key]
    if invalid_keys:
        raise ValueError("App Server thread_start_options keys must be non-empty strings")
    reserved = sorted({"cwd", "params"}.intersection(copied))
    if reserved:
        raise ValueError(
            "App Server thread_start_options cannot override adapter-owned fields: "
            + ", ".join(reserved)
        )
    aliases = {
        "approval_policy": "approvalPolicy",
        "approvals_reviewer": "approvalsReviewer",
        "sandbox_policy": "sandboxPolicy",
        "service_name": "serviceName",
        "thread_id": "threadId",
    }
    normalized_keys = [aliases.get(key, key) for key in copied]
    if len(set(normalized_keys)) != len(normalized_keys):
        raise ValueError("App Server thread_start_options contain duplicate native fields")
    return MappingProxyType(copied)


def _creation_profiles(
    profiles: Mapping[str, Mapping[str, object]] | None,
) -> tuple[Mapping[str, Mapping[str, object]], tuple[ThreadCreationProfile, ...]]:
    if profiles is None:
        return MappingProxyType({}), ()
    if not isinstance(profiles, Mapping) or len(profiles) > MAX_THREAD_CREATION_PROFILES:
        raise ValueError("App Server creation profiles must be a bounded mapping")
    copied: dict[str, Mapping[str, object]] = {}
    declared: list[ThreadCreationProfile] = []
    for profile_id, options in profiles.items():
        if not isinstance(options, Mapping):
            raise ValueError("App Server creation profile options must be a mapping")
        native = _thread_start_options(options)
        encoded = json.dumps(dict(native), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("App Server creation profile options exceed byte limit")
        profile = ThreadCreationProfile(
            profile_id, hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        )
        validate_thread_creation_profile(profile)
        # Keep only JSON values owned by this adapter. Each dispatch deep-copies again.
        copied[profile_id] = MappingProxyType(json.loads(encoded))
        declared.append(profile)
    return MappingProxyType(copied), tuple(declared)

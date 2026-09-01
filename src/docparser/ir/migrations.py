"""Pure deterministic migration registry for supported IR schema versions."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

Migration = Callable[[Mapping[str, Any]], dict[str, Any]]


def _identity_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(payload))


def _v1_0_to_v1_1(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(dict(payload))
    migrated["schema_version"] = "1.1.0"
    return migrated


_MIGRATIONS: dict[tuple[str, str], Migration] = {
    ("1.0.0", "1.0.0"): _identity_v1,
    ("1.0.0", "1.1.0"): _v1_0_to_v1_1,
    ("1.1.0", "1.1.0"): _identity_v1,
}


def migrate_ir(
    source_version: str,
    target_version: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a migrated copy without mutating the source payload."""

    migration = _MIGRATIONS.get((source_version, target_version))
    if migration is None:
        raise ValueError(f"unsupported IR migration: {source_version} -> {target_version}")
    if payload.get("schema_version") != source_version:
        raise ValueError("payload schema_version does not match source_version")
    return migration(payload)

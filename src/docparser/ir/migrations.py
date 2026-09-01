"""Pure deterministic migration registry for supported IR schema versions."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

Migration = Callable[[Mapping[str, Any]], dict[str, Any]]


def _identity_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(payload))


_MIGRATIONS: dict[tuple[str, str], Migration] = {
    ("1.0.0", "1.0.0"): _identity_v1,
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

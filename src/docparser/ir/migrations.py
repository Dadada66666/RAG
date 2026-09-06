"""Pure deterministic migration registry for supported IR schema versions."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any, Literal

Migration = Callable[[Mapping[str, Any]], dict[str, Any]]
CURRENT_SCHEMA_VERSION: Literal["1.3.0"] = "1.3.0"


def _identity_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(payload))


def _v1_0_to_v1_1(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(dict(payload))
    migrated["schema_version"] = "1.1.0"
    return migrated


def _v1_1_to_v1_2(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(dict(payload))
    migrated["schema_version"] = "1.2.0"
    return migrated


def _v1_2_to_v1_3(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(dict(payload))
    for table in migrated.get("tables", []):
        header_rows = set(table.get("header_row_indices", []))
        for cell in table.get("cells", []):
            if not cell.get("is_header", False):
                cell["header_role"] = "NONE"
            elif cell.get("row_index") in header_rows:
                cell["header_role"] = "COLUMN_HEADER"
            else:
                cell["header_role"] = "UNKNOWN"
    migrated["schema_version"] = CURRENT_SCHEMA_VERSION
    return migrated


def _v1_0_to_v1_2(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _v1_1_to_v1_2(_v1_0_to_v1_1(payload))


def _v1_0_to_v1_3(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _v1_2_to_v1_3(_v1_0_to_v1_2(payload))


def _v1_1_to_v1_3(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _v1_2_to_v1_3(_v1_1_to_v1_2(payload))


_MIGRATIONS: dict[tuple[str, str], Migration] = {
    ("1.0.0", "1.0.0"): _identity_v1,
    ("1.0.0", "1.1.0"): _v1_0_to_v1_1,
    ("1.0.0", "1.2.0"): _v1_0_to_v1_2,
    ("1.0.0", CURRENT_SCHEMA_VERSION): _v1_0_to_v1_3,
    ("1.1.0", "1.1.0"): _identity_v1,
    ("1.1.0", "1.2.0"): _v1_1_to_v1_2,
    ("1.1.0", CURRENT_SCHEMA_VERSION): _v1_1_to_v1_3,
    ("1.2.0", "1.2.0"): _identity_v1,
    ("1.2.0", CURRENT_SCHEMA_VERSION): _v1_2_to_v1_3,
    (CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION): _identity_v1,
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

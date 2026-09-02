"""Deterministic Canonical IR JSON serialization and semantic hashing."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from typing import Any

from docparser.ir.migrations import migrate_ir
from docparser.ir.models import DocumentIR
from docparser.ir.types import Sha256Digest

_SEMANTIC_EXCLUDED_TOP_LEVEL_FIELDS = frozenset({"created_at"})


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        normalized_key = unicodedata.normalize("NFC", key)
        if normalized_key in result:
            raise ValueError(f"duplicate JSON key: {normalized_key}")
        result[normalized_key] = value
    return result


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON cannot contain NaN or Infinity")
        return 0.0 if value == 0.0 else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise ValueError(f"JSON keys collide after NFC normalization: {normalized_key}")
            normalized[normalized_key] = _normalize_json(item)
        return normalized
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _encode_canonical_json(value: Any) -> bytes:
    normalized = _normalize_json(value)
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


def dump_canonical_json(document: DocumentIR) -> bytes:
    """Serialize a validated IR to deterministic UTF-8 JSON bytes."""

    return _encode_canonical_json(document.model_dump(mode="json"))


def load_canonical_json(data: bytes | str) -> DocumentIR:
    """Decode canonical JSON while rejecting duplicate keys and non-finite numbers."""

    text = data.decode("utf-8") if isinstance(data, bytes) else data
    payload = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("Canonical IR JSON root must be an object")
    source_version = payload.get("schema_version")
    if source_version in {"1.0.0", "1.1.0"}:
        payload = migrate_ir(source_version, "1.2.0", payload)
    return DocumentIR.model_validate_json(_encode_canonical_json(payload))


def validate_ir(value: DocumentIR | Mapping[str, Any] | bytes | str) -> DocumentIR:
    """Validate an IR instance or serialized/mapping representation."""

    if isinstance(value, DocumentIR):
        return value
    if isinstance(value, (bytes, str)):
        return load_canonical_json(value)
    return load_canonical_json(_encode_canonical_json(dict(value)))


def semantic_digest(document: DocumentIR) -> Sha256Digest:
    """Hash semantic content, excluding only contract-declared operational fields."""

    payload = document.model_dump(mode="json")
    for field_name in _SEMANTIC_EXCLUDED_TOP_LEVEL_FIELDS:
        payload.pop(field_name, None)
    digest = hashlib.sha256(_encode_canonical_json(payload)).hexdigest()
    return Sha256Digest(f"sha256:{digest}")

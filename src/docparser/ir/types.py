"""Strict wire primitives shared by Canonical IR models."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    GetCoreSchemaHandler,
    JsonValue,
    StringConstraints,
)
from pydantic_core import CoreSchema, core_schema

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
)
_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_EXTENSION_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_-]*){2,}$")

MAX_JSON_DEPTH = 5
MAX_JSON_KEYS = 64
MAX_ENTITY_JSON_BYTES = 16 * 1024
MAX_DOCUMENT_EXTENSION_BYTES = 1024 * 1024


def normalize_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize_non_empty(value: str) -> str:
    normalized = normalize_nfc(value)
    if not normalized:
        raise ValueError("string must not be empty")
    return normalized


NfcString = Annotated[str, AfterValidator(normalize_nfc)]
NonEmptyNfcString = Annotated[str, Field(min_length=1), AfterValidator(_normalize_non_empty)]


def _validate_language_tag(value: str) -> str:
    normalized = _normalize_non_empty(value)
    if _LANGUAGE_TAG_RE.fullmatch(normalized) is None:
        raise ValueError("language must be a BCP 47 language tag")
    return normalized


LanguageTag = Annotated[str, AfterValidator(_validate_language_tag)]


class Sha256Digest(str):
    """Lowercase, algorithm-prefixed SHA-256 digest."""

    def __new__(cls, value: str) -> Sha256Digest:
        if not isinstance(value, str):
            raise TypeError("Sha256Digest must be a string")
        if _DIGEST_RE.fullmatch(value) is None:
            raise ValueError("digest must match sha256:<64 lowercase hex>")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(strict=True, pattern=_DIGEST_RE.pattern),
            serialization=core_schema.to_string_ser_schema(),
        )


class UtcTimestamp(str):
    """RFC 3339 UTC timestamp whose wire form always ends in ``Z``."""

    def __new__(cls, value: str) -> UtcTimestamp:
        if not isinstance(value, str):
            raise TypeError("UtcTimestamp must be a string")
        if _UTC_TIMESTAMP_RE.fullmatch(value) is None:
            raise ValueError("timestamp must be RFC 3339 UTC with a Z suffix")
        try:
            datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError as exc:
            raise ValueError("timestamp contains an invalid calendar date or time") from exc
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(strict=True, pattern=_UTC_TIMESTAMP_RE.pattern),
            serialization=core_schema.to_string_ser_schema(),
        )


def _normalize_json_value(value: object, *, depth: int, key_counter: list[int]) -> JsonValue:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"JSON value depth exceeds {MAX_JSON_DEPTH}")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return normalize_nfc(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return 0.0 if value == 0.0 else value
    if isinstance(value, list):
        return [
            _normalize_json_value(item, depth=depth + 1, key_counter=key_counter)
            for item in value
        ]
    if isinstance(value, dict):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            normalized_key = normalize_nfc(key)
            if normalized_key in normalized:
                raise ValueError("JSON object keys collide after NFC normalization")
            key_counter[0] += 1
            if key_counter[0] > MAX_JSON_KEYS:
                raise ValueError(f"JSON object exceeds {MAX_JSON_KEYS} total keys")
            normalized[normalized_key] = _normalize_json_value(
                item,
                depth=depth + 1,
                key_counter=key_counter,
            )
        return normalized
    raise ValueError("value must contain JSON-compatible types only")


def _validate_bounded_json_object(value: object) -> object:
    if not isinstance(value, dict):
        raise ValueError("value must be a JSON object")
    normalized = _normalize_json_value(value, depth=0, key_counter=[0])
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_ENTITY_JSON_BYTES:
        raise ValueError(f"JSON object exceeds {MAX_ENTITY_JSON_BYTES} UTF-8 bytes")
    return normalized


def _validate_extensions(value: object) -> object:
    normalized = _validate_bounded_json_object(value)
    assert isinstance(normalized, dict)
    invalid_keys = [key for key in normalized if _EXTENSION_KEY_RE.fullmatch(key) is None]
    if invalid_keys:
        raise ValueError(f"extension key is not namespaced: {invalid_keys[0]}")
    return normalized


ExtensionKey = Annotated[str, StringConstraints(pattern=_EXTENSION_KEY_RE.pattern)]
BoundedJsonObject = Annotated[
    dict[str, JsonValue],
    BeforeValidator(_validate_bounded_json_object),
    Field(max_length=MAX_JSON_KEYS),
]
Extensions = Annotated[
    dict[ExtensionKey, JsonValue],
    BeforeValidator(_validate_extensions),
    Field(
        max_length=MAX_JSON_KEYS,
        json_schema_extra={"additionalProperties": False},
    ),
]

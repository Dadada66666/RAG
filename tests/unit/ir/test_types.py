from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from docparser.ir.types import (
    BoundedJsonObject,
    Extensions,
    LanguageTag,
    Sha256Digest,
    UtcTimestamp,
)


def test_digest_requires_lowercase_prefixed_sha256() -> None:
    assert Sha256Digest(f"sha256:{'0' * 64}") == f"sha256:{'0' * 64}"

    with pytest.raises(ValueError, match="lowercase hex"):
        Sha256Digest(f"sha256:{'A' * 64}")
    with pytest.raises(TypeError, match="string"):
        Sha256Digest(123)  # type: ignore[arg-type]


def test_timestamp_requires_valid_utc_calendar_value() -> None:
    assert UtcTimestamp("2026-08-28T08:30:00.123Z").endswith("Z")

    with pytest.raises(ValueError, match="calendar"):
        UtcTimestamp("2026-02-30T08:30:00Z")
    with pytest.raises(TypeError, match="string"):
        UtcTimestamp(123)  # type: ignore[arg-type]


def test_language_tag_validation() -> None:
    adapter = TypeAdapter(LanguageTag)

    assert adapter.validate_python("zh-Hans") == "zh-Hans"
    with pytest.raises(ValidationError, match="BCP 47"):
        adapter.validate_python("not_a_language")


def test_extensions_are_namespaced_bounded_json_and_nfc() -> None:
    adapter = TypeAdapter(Extensions)
    result = adapter.validate_python(
        {"org.example.label": {"value": "Cafe\u0301", "score": 0.5}}
    )

    extension = result["org.example.label"]
    assert isinstance(extension, dict)
    assert extension["value"] == "Café"


@pytest.mark.parametrize(
    "value",
    [
        {"raw": "forbidden"},
        {"org.example.value": math.nan},
        {"org.example.value": ("tuple",)},
        {"org.example.value": {1: "non-string key"}},
        {"org.example.value": {"e\u0301": 1, "é": 2}},
        {"org.example.value": [[[[[["too deep"]]]]]]},
        {"org.example.value": "x" * (16 * 1024)},
    ],
)
def test_invalid_extension_payload_is_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Extensions).validate_python(value)


def test_bounded_json_object_rejects_excessive_keys() -> None:
    payload = {f"key-{index}": index for index in range(65)}

    with pytest.raises(ValidationError, match="64"):
        TypeAdapter(BoundedJsonObject).validate_python(payload)


def test_bounded_json_object_accepts_all_json_scalars() -> None:
    payload = {
        "none": None,
        "bool": True,
        "int": 1,
        "float": -0.0,
        "list": ["text", 2],
    }

    result = TypeAdapter(BoundedJsonObject).validate_python(payload)

    assert result["float"] == 0.0

from __future__ import annotations

import json
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.ir_factory import make_block, make_document

from docparser.ir.serialization import (
    dump_canonical_json,
    load_canonical_json,
    semantic_digest,
    validate_ir,
)


def test_canonical_json_round_trip_is_equivalent_and_byte_stable() -> None:
    document = make_document()

    first = dump_canonical_json(document)
    second = dump_canonical_json(document)
    restored = load_canonical_json(first)

    assert first == second
    assert restored == document
    assert dump_canonical_json(restored) == first


def test_validate_ir_accepts_json_like_mapping_without_scalar_coercion() -> None:
    document = make_document()
    mapping = json.loads(dump_canonical_json(document))

    assert validate_ir(mapping) == document
    mapping["page_count"] = "1"
    with pytest.raises(ValidationError, match="page_count"):
        validate_ir(mapping)


def test_validate_ir_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        validate_ir({1: "invalid"})  # type: ignore[dict-item]


@settings(max_examples=30)
@given(
    text=st.text(
        alphabet=st.characters(codec="utf-8"),
        min_size=1,
        max_size=60,
    )
)
def test_canonical_json_property_is_deterministic_and_nfc(text: str) -> None:
    document = make_document(blocks=(make_block(text=text),))

    encoded = dump_canonical_json(document)
    restored = load_canonical_json(encoded)

    assert dump_canonical_json(restored) == encoded
    assert restored.pages[0].blocks[0].text == unicodedata.normalize("NFC", text)


def test_semantic_digest_excludes_created_at_only() -> None:
    first = make_document(created_at="2026-08-28T08:30:00Z")
    second = make_document(created_at="2026-08-29T09:45:00Z")
    changed_text = make_document(blocks=(make_block(text="Different semantic content"),))

    assert semantic_digest(first) == semantic_digest(second)
    assert semantic_digest(first) != semantic_digest(changed_text)


def test_semantic_digest_golden_vector() -> None:
    assert semantic_digest(make_document()) == (
        "sha256:8023ed2642d5c140510ac2790c8f226b60c8c1b3df951afc245ead63927cda72"
    )


def test_duplicate_json_keys_are_rejected_before_model_validation() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_canonical_json('{"schema_version":"1.0.0","schema_version":"1.0.0"}')


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_are_rejected(constant: str) -> None:
    payload = dump_canonical_json(make_document()).decode("utf-8")
    parsed = json.loads(payload)
    parsed["pages"][0]["width"] = constant
    invalid = json.dumps(parsed).replace(f'"{constant}"', constant, 1)

    with pytest.raises(ValueError, match="non-finite"):
        load_canonical_json(invalid)


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(UnicodeDecodeError):
        load_canonical_json(b"\xff")


def test_serialized_coordinates_use_bounded_precision() -> None:
    document = make_document(blocks=(make_block(bbox=None),))
    payload = json.loads(dump_canonical_json(document))

    assert payload["pages"][0]["width"] == 595.276
    assert payload["pages"][0]["blocks"][0]["bbox"] == [50.0, 60.0, 545.0, 100.0]

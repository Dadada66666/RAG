from __future__ import annotations

import copy
import json
import math
import unicodedata
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError
from tests.ir_factory import (
    BLOCK_PROVENANCE_ID,
    TEST_NAMESPACE,
    make_block,
    make_document,
)

from docparser.ir.geometry import BBox
from docparser.ir.ids import PageId, ProvenanceId, generate_uuid5_id
from docparser.ir.models import DocumentIR, TextSpan
from docparser.ir.serialization import dump_canonical_json


def _payload(document: DocumentIR | None = None) -> dict[str, Any]:
    loaded = json.loads(dump_canonical_json(document or make_document()))
    assert isinstance(loaded, dict)
    return loaded


def _validate(payload: dict[str, Any]) -> DocumentIR:
    return DocumentIR.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_minimal_one_page_document_is_valid() -> None:
    document = make_document()

    assert document.page_count == 1
    assert document.pages[0].blocks[0].text == "年度报告 / Annual Report"


def test_multi_block_page_has_contiguous_reading_order() -> None:
    document = make_document(blocks=(make_block(ordinal=0), make_block(ordinal=1)))

    assert [block.reading_order for block in document.pages[0].blocks] == [0, 1]


def test_unicode_is_nfc_without_destructive_normalization() -> None:
    decomposed = "Cafe\u0301，年度报告!"
    document = make_document(blocks=(make_block(text=decomposed),))

    assert document.pages[0].blocks[0].text == unicodedata.normalize("NFC", decomposed)
    assert document.pages[0].blocks[0].text == "Café，年度报告!"


def test_text_span_uses_unicode_code_point_offsets() -> None:
    text = "收入📈 grew"
    span = TextSpan(
        start=0,
        end=len(text),
        bbox=BBox((50.0, 60.0, 545.0, 100.0)),
        style=None,
        language="zh-Hans",
        provenance_ids=(BLOCK_PROVENANCE_ID,),
    )
    document = make_document(blocks=(make_block(text=text, text_spans=(span,)),))

    assert document.pages[0].blocks[0].text_spans[0].end == len(text)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.update({"unknown": True}), "Extra inputs"),
        (
            lambda data: data["pages"][0].update({"page_number": "1"}),
            "valid integer",
        ),
        (
            lambda data: data["pages"][0].update({"page_number": 0}),
            "greater than or equal",
        ),
        (
            lambda data: data.update({"page_count": 0}),
            "greater than or equal",
        ),
        (
            lambda data: data["source"].update({"sha256": f"sha256:{'A' * 64}"}),
            "pattern",
        ),
        (
            lambda data: data.update({"created_at": "2026-08-28T08:30:00+00:00"}),
            "pattern",
        ),
        (
            lambda data: data.update({"document_id": "doc_not-a-uuid"}),
            "pattern",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"bbox": [50.0, 60.0, 50.0, 100.0]}),
            "positive width",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"bbox": [50.0, 60.0, 700.0, 100.0]}),
            "outside canonical page",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"page_number": 2}),
            "containing page",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"confidence": -0.01}),
            "greater than or equal",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"confidence": 1.01}),
            "less than or equal",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update(
                {"extensions": {"raw": {"parser": "payload"}}}
            ),
            "namespaced",
        ),
        (
            lambda data: data["pages"][0]["blocks"][0].update({"provenance_ids": []}),
            "at least 1",
        ),
    ],
)
def test_invalid_wire_values_are_rejected(
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        _validate(payload)


def test_missing_page_is_rejected() -> None:
    payload = _payload()
    payload["page_count"] = 2

    with pytest.raises(ValidationError, match=r"len\(pages\)"):
        _validate(payload)


def test_duplicate_page_number_is_rejected() -> None:
    payload = _payload()
    page = copy.deepcopy(payload["pages"][0])
    payload["page_count"] = 2
    payload["pages"].append(page)

    with pytest.raises(ValidationError, match="numbered exactly"):
        _validate(payload)


def test_page_id_must_match_document_and_page_number() -> None:
    payload = _payload()
    wrong_page_id = generate_uuid5_id(PageId, TEST_NAMESPACE, "wrong-page")
    payload["pages"][0]["page_id"] = str(wrong_page_id)

    with pytest.raises(ValidationError, match="page_id does not match"):
        _validate(payload)


def test_reading_order_must_be_unique_and_contiguous() -> None:
    payload = _payload(make_document(blocks=(make_block(ordinal=0), make_block(ordinal=1))))
    payload["pages"][0]["blocks"][1]["reading_order"] = 0

    with pytest.raises(ValidationError, match="unique and contiguous"):
        _validate(payload)


def test_non_flow_block_cannot_declare_reading_order() -> None:
    payload = _payload()
    payload["pages"][0]["blocks"][0]["reading_order_status"] = "DECORATIVE"

    with pytest.raises(ValidationError, match="must not declare"):
        _validate(payload)


def test_broken_provenance_reference_is_rejected() -> None:
    payload = _payload()
    missing_id = generate_uuid5_id(ProvenanceId, TEST_NAMESPACE, "missing-provenance")
    payload["pages"][0]["blocks"][0]["provenance_ids"] = [str(missing_id)]

    with pytest.raises(ValidationError, match="does not resolve"):
        _validate(payload)


def test_broken_parent_provenance_reference_is_rejected() -> None:
    payload = _payload()
    missing_id = generate_uuid5_id(ProvenanceId, TEST_NAMESPACE, "missing-parent")
    payload["provenance"][1]["parent_provenance_ids"] = [str(missing_id)]

    with pytest.raises(ValidationError, match="parent provenance"):
        _validate(payload)


def test_overlapping_text_spans_are_rejected() -> None:
    payload = _payload()
    payload["pages"][0]["blocks"][0]["text_spans"] = [
        {
            "start": 0,
            "end": 4,
            "bbox": None,
            "style": None,
            "language": "en",
            "provenance_ids": [str(BLOCK_PROVENANCE_ID)],
        },
        {
            "start": 3,
            "end": 6,
            "bbox": None,
            "style": None,
            "language": "en",
            "provenance_ids": [str(BLOCK_PROVENANCE_ID)],
        },
    ]

    with pytest.raises(ValidationError, match="non-overlapping"):
        _validate(payload)


def test_text_span_outside_text_is_rejected() -> None:
    payload = _payload()
    payload["pages"][0]["blocks"][0]["text_spans"] = [
        {
            "start": 0,
            "end": 1000,
            "bbox": None,
            "style": None,
            "language": "en",
            "provenance_ids": [str(BLOCK_PROVENANCE_ID)],
        }
    ]

    with pytest.raises(ValidationError, match="outside block text"):
        _validate(payload)


@pytest.mark.parametrize("invalid_number", [math.nan, math.inf, -math.inf])
def test_non_finite_page_dimension_is_rejected(invalid_number: float) -> None:
    payload = _payload()
    payload["pages"][0]["width"] = invalid_number

    with pytest.raises(ValidationError, match="finite"):
        _validate(payload)


def test_self_intersecting_polygon_is_rejected() -> None:
    payload = _payload()
    payload["pages"][0]["blocks"][0]["polygon"] = [
        [50.0, 60.0],
        [100.0, 100.0],
        [50.0, 100.0],
        [100.0, 60.0],
    ]

    with pytest.raises(ValidationError, match="non-self-intersecting"):
        _validate(payload)


def test_polygon_points_must_lie_on_page() -> None:
    block = make_block()
    payload = _payload(make_document(blocks=(block,)))
    payload["pages"][0]["blocks"][0]["polygon"] = [
        [50.0, 60.0],
        [700.0, 60.0],
        [50.0, 100.0],
    ]

    with pytest.raises(ValidationError, match="polygon lies outside"):
        _validate(payload)


def test_document_extension_budget_is_enforced() -> None:
    bbox = BBox((50.0, 60.0, 545.0, 100.0))
    blocks = tuple(
        make_block(ordinal=index, bbox=bbox).model_copy(
            update={"extensions": {"org.example.payload": "x" * 15_000}}
        )
        for index in range(70)
    )

    with pytest.raises(ValidationError, match="1 MiB"):
        make_document(blocks=blocks)

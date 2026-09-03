from __future__ import annotations

import pytest
from tests.parser_fixture import load_contract_result

from docparser.domain.parser_contract import ExtractedElementType


@pytest.mark.parametrize(
    "fixture",
    [
        "born-digital",
        "two-column",
        "scanned",
        "simple-table",
        "merged-table",
        "rotated",
        "bilingual",
    ],
)
def test_synthetic_docling_contract_maps_to_neutral_result(fixture: str) -> None:
    result = load_contract_result(fixture)

    assert result.pages_requested == (1,)
    assert result.pages[0].elements
    assert result.run.actual_device.value == "cpu"


def test_docling_body_order_is_preserved_for_two_columns() -> None:
    result = load_contract_result("two-column")

    assert [element.reading_order for element in result.pages[0].elements] == [0, 1, 2, 3]


def test_real_wire_refs_expand_groups_and_preserve_body_order() -> None:
    page = load_contract_result("real-wire-refs").pages[0]
    elements = {element.source_object_id: element for element in page.elements}

    assert elements["#/texts/0"].reading_order == 0
    assert elements["#/texts/1"].reading_order == 1
    assert elements["#/tables/0"].reading_order == 2
    assert elements["#/texts/4"].reading_order is None
    assert not elements["#/texts/4"].reading_order_resolved


def test_real_wire_parent_ref_is_preserved() -> None:
    page = load_contract_result("real-wire-refs").pages[0]
    elements = {element.source_object_id: element for element in page.elements}

    assert elements["#/texts/1"].parent_source_object_id == "#/texts/0"


def test_real_wire_table_and_picture_caption_refs_are_preserved() -> None:
    page = load_contract_result("real-wire-refs").pages[0]
    elements = {element.source_object_id: element for element in page.elements}

    assert page.tables[0].caption_source_object_ids == ("#/texts/2",)
    assert elements["#/texts/2"].caption_for_source_object_id == "#/tables/0"
    assert elements["#/texts/3"].caption_for_source_object_id == "#/pictures/0"


def test_table_is_structured_not_flattened() -> None:
    page = load_contract_result("merged-table").pages[0]

    assert page.elements[0].element_type is ExtractedElementType.TABLE
    assert page.tables[0].cells[0].column_span == 2


def test_docling_parent_evidence_is_not_discarded() -> None:
    page = load_contract_result("born-digital").pages[0]

    assert page.elements[1].parent_source_object_id == page.elements[0].source_object_id
    assert all(element.extraction_method == "IMPORTED" for element in page.elements)

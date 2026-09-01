from __future__ import annotations

import pytest
from tests.parser_fixture import load_recorded_result

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
def test_recorded_docling_output_maps_to_neutral_result(fixture: str) -> None:
    result = load_recorded_result(fixture)

    assert result.pages_requested == (1,)
    assert result.pages[0].elements
    assert result.run.actual_device.value == "cpu"


def test_docling_body_order_is_preserved_for_two_columns() -> None:
    result = load_recorded_result("two-column")

    assert [element.reading_order for element in result.pages[0].elements] == [0, 1, 2, 3]


def test_table_is_structured_not_flattened() -> None:
    page = load_recorded_result("merged-table").pages[0]

    assert page.elements[0].element_type is ExtractedElementType.TABLE
    assert page.tables[0].cells[0].column_span == 2


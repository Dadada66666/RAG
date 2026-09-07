from __future__ import annotations

import json
from pathlib import Path

import pytest

from docparser.adapters.parsers.paddleocr_vl.mapping import (
    PADDLE_LABEL_CONTRACT_VERSION,
    map_paddleocr_vl_pages,
    table_cells_from_html,
)
from docparser.adapters.parsers.paddleocr_vl.options import ADAPTER_VERSION
from docparser.domain.parser_contract import (
    ExtractedElementType,
    ParserCapability,
    ParserDescriptor,
    ParserRun,
    RuntimeDevice,
)
from docparser.ir.enums import TableCellHeaderRole
from docparser.ir.ids import ParserRunId
from docparser.ir.types import UtcTimestamp


def _descriptor() -> ParserDescriptor:
    return ParserDescriptor(
        parser_name="paddleocr-vl",
        parser_version="3.7.0",
        adapter_id="org.docparser.adapter.paddleocr-vl",
        adapter_version=ADAPTER_VERSION,
        profile="paddleocr-vl-1.6",
        capabilities=tuple(ParserCapability),
        model_identifiers=("PP-DocLayoutV3", "PaddleOCR-VL-1.6-0.9B"),
    )


def _run() -> ParserRun:
    return ParserRun(
        parser_run_id=ParserRunId("prun_018bcfe5-6800-7000-8000-000000000081"),
        started_at=UtcTimestamp("2026-09-01T00:00:00Z"),
        ended_at=UtcTimestamp("2026-09-01T00:00:01Z"),
        requested_device=RuntimeDevice.CUDA,
        actual_device=RuntimeDevice.CUDA,
        determinism="BEST_EFFORT",
        runtime={"org.docparser.pipeline_version": "v1.6"},
    )


def test_html_table_preserves_rowspan_and_colspan_without_fake_bbox() -> None:
    rows, columns, cells = table_cells_from_html(
        "<table><tr><th rowspan='2'>A</th><th colspan='2'>B</th></tr>"
        "<tr><td>C</td><td>D</td></tr></table>",
        table_id="table-1",
    )

    assert (rows, columns) == (2, 3)
    assert (cells[0].row_span, cells[1].column_span) == (2, 2)
    assert all(cell.bbox is None for cell in cells)
    assert cells[0].header_role is TableCellHeaderRole.UNKNOWN
    assert cells[1].header_role is TableCellHeaderRole.UNKNOWN


def test_structured_paddle_fixture_maps_without_markdown_contract() -> None:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-structured.json").read_text(encoding="utf-8")
    )
    result = map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())

    assert result.pages_requested == (1,)
    assert result.pages[0].elements[0].reading_order == 0
    assert result.pages[0].elements[0].extraction_method == "VLM"
    assert result.pages[0].tables[0].row_count == 3
    assert result.pages[0].tables[0].cells[0].row_span == 2
    assert result.pages[0].tables[0].cells[1].column_span == 2


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("doc_title", ExtractedElementType.TITLE),
        ("paragraph_title", ExtractedElementType.HEADING),
        ("abstract_title", ExtractedElementType.HEADING),
        ("reference_title", ExtractedElementType.HEADING),
        ("refer_title", ExtractedElementType.HEADING),
        ("content_title", ExtractedElementType.HEADING),
        ("text", ExtractedElementType.PARAGRAPH),
        ("paragraph", ExtractedElementType.PARAGRAPH),
        ("content", ExtractedElementType.PARAGRAPH),
        ("abstract", ExtractedElementType.PARAGRAPH),
        ("reference", ExtractedElementType.PARAGRAPH),
        ("reference_content", ExtractedElementType.PARAGRAPH),
        ("aside_text", ExtractedElementType.PARAGRAPH),
        ("list", ExtractedElementType.LIST),
        ("table", ExtractedElementType.TABLE),
        ("image", ExtractedElementType.FIGURE),
        ("figure", ExtractedElementType.FIGURE),
        ("chart", ExtractedElementType.FIGURE),
        ("flowchart", ExtractedElementType.FIGURE),
        ("seal", ExtractedElementType.FIGURE),
        ("table_title", ExtractedElementType.FIGURE_CAPTION),
        ("table_caption", ExtractedElementType.FIGURE_CAPTION),
        ("chart_title", ExtractedElementType.FIGURE_CAPTION),
        ("figure_title", ExtractedElementType.FIGURE_CAPTION),
        ("figure_table_chart_title", ExtractedElementType.FIGURE_CAPTION),
        ("figure_caption", ExtractedElementType.FIGURE_CAPTION),
        ("image_caption", ExtractedElementType.FIGURE_CAPTION),
        ("formula", ExtractedElementType.EQUATION),
        ("display_formula", ExtractedElementType.EQUATION),
        ("inline_formula", ExtractedElementType.EQUATION),
        ("formula_number", ExtractedElementType.UNKNOWN),
        ("algorithm", ExtractedElementType.CODE),
        ("footnote", ExtractedElementType.FOOTNOTE),
        ("vision_footnote", ExtractedElementType.FOOTNOTE),
        ("header", ExtractedElementType.HEADER),
        ("header_image", ExtractedElementType.HEADER),
        ("footer", ExtractedElementType.FOOTER),
        ("footer_image", ExtractedElementType.FOOTER),
        ("number", ExtractedElementType.PAGE_NUMBER),
    ],
)
def test_pinned_paddle_label_contract_is_explicit(
    label: str, expected: ExtractedElementType
) -> None:
    content = (
        "<table><tr><td>cell</td></tr></table>" if label == "table" else "Synthetic text"
    )
    result = map_paddleocr_vl_pages(
        [
            {
                "page_index": 0,
                "source_width": 100,
                "source_height": 100,
                "parsing_res_list": [
                    {
                        "block_id": 1,
                        "block_order": 0,
                        "block_label": label,
                        "block_bbox": [1, 1, 99, 99],
                        "block_content": content,
                    }
                ],
            }
        ],
        descriptor=_descriptor(),
        run=_run(),
    )

    element = result.pages[0].elements[0]
    assert PADDLE_LABEL_CONTRACT_VERSION == "PaddleX-3.7.1/PP-DocLayoutV3"
    assert element.element_type is expected
    assert element.metadata["org.paddleocr.label"] == label
    assert result.warnings == ()


def test_future_label_is_unknown_with_deterministic_warning() -> None:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-label-contract.json").read_text(
            encoding="utf-8"
        )
    )

    result = map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())
    elements = {
        element.metadata["org.paddleocr.label"]: element for element in result.pages[0].elements
    }

    assert elements["future_widget"].element_type is ExtractedElementType.UNKNOWN
    assert elements["future_widget"].text == "Future evidence stays visible."
    assert result.warnings == ("page 1: unmapped Paddle labels: future_widget=1",)


def test_caption_relationships_use_only_explicit_compatible_parents() -> None:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-label-contract.json").read_text(
            encoding="utf-8"
        )
    )
    result = map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())
    elements = {
        element.metadata["org.paddleocr.label"]: element for element in result.pages[0].elements
    }

    assert elements["figure_title"].caption_for_source_object_id == "paddle:1:3"
    assert elements["table_title"].caption_for_source_object_id == "paddle:1:7"
    assert result.pages[0].tables[0].caption_source_object_ids == ("paddle:1:8",)
    assert elements["figure_caption"].parent_source_object_id == "paddle:1:2"
    assert elements["figure_caption"].caption_for_source_object_id is None


def test_formula_number_remains_evidence_without_becoming_equation() -> None:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-label-contract.json").read_text(
            encoding="utf-8"
        )
    )
    result = map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())
    formula_number = next(
        element
        for element in result.pages[0].elements
        if element.metadata["org.paddleocr.label"] == "formula_number"
    )

    assert formula_number.element_type is ExtractedElementType.UNKNOWN
    assert formula_number.text == "(1)"
    assert formula_number.extraction_method == "VLM"


def test_large_table_mapping_keeps_all_533_cells() -> None:
    html = "<table>" + "".join(
        "<tr>" + "".join(f"<td>{row}:{column}</td>" for column in range(13)) + "</tr>"
        for row in range(41)
    ) + "</table>"

    rows, columns, cells = table_cells_from_html(html, table_id="large-table")

    assert (rows, columns) == (41, 13)
    assert len(cells) == 533

from __future__ import annotations

import json
from pathlib import Path

from docparser.adapters.parsers.paddleocr_vl.mapping import (
    map_paddleocr_vl_pages,
    table_cells_from_html,
)
from docparser.domain.parser_contract import (
    ParserCapability,
    ParserDescriptor,
    ParserRun,
    RuntimeDevice,
)
from docparser.ir.ids import ParserRunId
from docparser.ir.types import UtcTimestamp


def _descriptor() -> ParserDescriptor:
    return ParserDescriptor(
        parser_name="paddleocr-vl",
        parser_version="3.7.0",
        adapter_id="org.docparser.adapter.paddleocr-vl",
        adapter_version="0.1.0",
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


def test_structured_paddle_fixture_maps_without_markdown_contract() -> None:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-structured.json").read_text(
            encoding="utf-8"
        )
    )
    result = map_paddleocr_vl_pages(
        fixture["pages"], descriptor=_descriptor(), run=_run()
    )

    assert result.pages_requested == (1,)
    assert result.pages[0].elements[0].reading_order == 0
    assert result.pages[0].elements[0].extraction_method == "VLM"
    assert result.pages[0].tables[0].row_count == 3
    assert result.pages[0].tables[0].cells[0].row_span == 2
    assert result.pages[0].tables[0].cells[1].column_span == 2

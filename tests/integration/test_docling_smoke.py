from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.pdf_factory import write_tiny_pdf

from docparser.application import ParsingConfig, parse_document
from docparser.domain.parser_contract import RuntimeDevice
from docparser.ir.enums import ReadingOrderStatus


@pytest.mark.integration
@pytest.mark.parser
def test_real_docling_cpu_smoke(tmp_path: Path) -> None:
    if os.environ.get("DOCPARSER_RUN_DOCLING_SMOKE") != "1":
        pytest.skip("set DOCPARSER_RUN_DOCLING_SMOKE=1 with .[docling] installed")

    document = parse_document(
        write_tiny_pdf(tmp_path / "docling-smoke.pdf"),
        ParsingConfig(device=RuntimeDevice.CPU),
    )

    assert document.page_count == 1
    assert all(block.provenance_ids for page in document.pages for block in page.blocks)
    in_flow = [
        block
        for page in document.pages
        for block in page.blocks
        if block.reading_order_status is ReadingOrderStatus.IN_FLOW
    ]
    assert in_flow
    assert [block.reading_order for block in in_flow] == list(range(len(in_flow)))


@pytest.mark.integration
@pytest.mark.parser
def test_real_docling_table_has_actual_cell_structure(tmp_path: Path) -> None:
    if os.environ.get("DOCPARSER_RUN_DOCLING_SMOKE") != "1":
        pytest.skip("set DOCPARSER_RUN_DOCLING_SMOKE=1 with .[docling] installed")

    document = parse_document(
        write_tiny_pdf(tmp_path / "docling-table.pdf", layout="table"),
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
    )

    assert document.tables
    assert document.tables[0].logical_row_count >= 2
    assert len(document.tables[0].cells) >= 4

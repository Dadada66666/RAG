from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.pdf_factory import write_tiny_pdf

from docparser.application import ParsingConfig, parse_document
from docparser.domain.parser_contract import RuntimeDevice


@pytest.mark.integration
@pytest.mark.parser
@pytest.mark.gpu
@pytest.mark.parametrize(
    "layout",
    ["single", "scanned", "bilingual"],
)
def test_real_paddle_document_cases(tmp_path: Path, layout: str) -> None:
    if os.environ.get("DOCPARSER_RUN_PADDLE_SMOKE") != "1":
        pytest.skip("set DOCPARSER_RUN_PADDLE_SMOKE=1 with Paddle GPU runtime installed")

    document = parse_document(
        write_tiny_pdf(tmp_path / f"paddle-{layout}.pdf", layout=layout),
        ParsingConfig(parser="paddleocr-vl-1.6", device=RuntimeDevice.CUDA),
    )
    assert document.page_count == 1


@pytest.mark.integration
@pytest.mark.parser
@pytest.mark.gpu
def test_real_paddle_merged_table_has_actual_cell_structure(tmp_path: Path) -> None:
    if os.environ.get("DOCPARSER_RUN_PADDLE_SMOKE") != "1":
        pytest.skip("set DOCPARSER_RUN_PADDLE_SMOKE=1 with Paddle GPU runtime installed")

    document = parse_document(
        write_tiny_pdf(tmp_path / "paddle-merged.pdf", layout="merged-table"),
        ParsingConfig(parser="paddleocr-vl-1.6", device=RuntimeDevice.CUDA),
    )
    assert document.tables
    assert any(
        cell.row_span > 1 or cell.column_span > 1
        for cell in document.tables[0].cells
    )

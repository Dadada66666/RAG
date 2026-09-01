from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.pdf_factory import write_tiny_pdf

from docparser.application import ParsingConfig, parse_document
from docparser.domain.parser_contract import RuntimeDevice


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


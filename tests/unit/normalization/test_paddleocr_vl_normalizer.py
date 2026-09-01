from __future__ import annotations

import json
from pathlib import Path

from tests.pdf_factory import write_tiny_pdf
from tests.unit.adapters.paddleocr_vl.test_mapping import _descriptor, _run

from docparser.adapters.parsers.paddleocr_vl.mapping import map_paddleocr_vl_pages
from docparser.application.parsing import ParsingConfig, parse_document_with_diagnostics
from docparser.domain.parser_contract import (
    ParserDescriptor,
    ParseRequest,
    ParseResult,
    ParserHealth,
    RuntimeDevice,
)
from docparser.ir.enums import ExtractionMethod


class _StaticPaddleParser:
    def __init__(self, result: ParseResult) -> None:
        self._result = result

    def descriptor(self) -> ParserDescriptor:
        return self._result.descriptor

    def health(self) -> ParserHealth:
        raise AssertionError("not used")

    def parse(self, request: ParseRequest) -> ParseResult:
        return self._result


def _result() -> ParseResult:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-structured.json").read_text(
            encoding="utf-8"
        )
    )
    return map_paddleocr_vl_pages(
        fixture["pages"], descriptor=_descriptor(), run=_run()
    )


def test_pixels_scale_to_cropbox_points_and_bboxless_cells_keep_cell_provenance(
    tmp_path: Path,
) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    outcome = parse_document_with_diagnostics(
        source,
        ParsingConfig(parser="paddleocr-vl-1.6", device=RuntimeDevice.CUDA),
        parser=_StaticPaddleParser(_result()),
    )
    page = outcome.document.pages[0]
    table = outcome.document.tables[0]
    provenance = {record.provenance_id: record for record in outcome.document.provenance}

    assert page.width == 612.0
    assert table.segments[0].bbox.root == (60.0, 120.0, 550.0, 380.0)
    assert all(cell.bbox is None for cell in table.cells)
    for index, cell in enumerate(table.cells):
        original_id = provenance[cell.provenance_ids[0]].original_object_id
        assert original_id is not None
        assert original_id.endswith(f"/cell/{index}")
    assert all(provenance[cell.provenance_ids[0]].bbox is None for cell in table.cells)
    assert outcome.diagnostics.table_cells_without_bbox == len(table.cells)
    assert outcome.diagnostics.numeric_disagreement_count > 0
    assert outcome.diagnostics.numeric_disagreements[0].code == (
        "NUMERIC_TEXT_DISAGREEMENT"
    )
    assert any(
        record.extraction_method is ExtractionMethod.VLM
        for record in outcome.document.provenance
    )

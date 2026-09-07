from __future__ import annotations

import json
from pathlib import Path

from tests.parser_fixture import normalization_context, profile_for_result
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
from docparser.ir.enums import BlockType, ExtractionMethod, ReadingOrderStatus
from docparser.ir.serialization import dump_canonical_json
from docparser.normalization import normalize_neutral_result, normalize_paddleocr_vl_result


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
        Path("tests/fixtures/paddleocr_vl/synthetic-structured.json").read_text(encoding="utf-8")
    )
    return map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())


def _label_contract_result() -> ParseResult:
    fixture = json.loads(
        Path("tests/fixtures/paddleocr_vl/synthetic-label-contract.json").read_text(
            encoding="utf-8"
        )
    )
    return map_paddleocr_vl_pages(fixture["pages"], descriptor=_descriptor(), run=_run())


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
    assert table.header_row_indices == ()
    assert all(cell.bbox is None for cell in table.cells)
    for index, cell in enumerate(table.cells):
        original_id = provenance[cell.provenance_ids[0]].original_object_id
        assert original_id is not None
        assert original_id.endswith(f"/cell/{index}")
    assert all(provenance[cell.provenance_ids[0]].bbox is None for cell in table.cells)
    assert outcome.diagnostics.table_cells_without_bbox == len(table.cells)
    assert outcome.diagnostics.numeric_disagreement_count > 0
    assert outcome.diagnostics.numeric_disagreements[0].code == ("NUMERIC_TEXT_DISAGREEMENT")
    assert any(
        record.extraction_method is ExtractionMethod.VLM for record in outcome.document.provenance
    )


def test_paddle_entrypoint_has_neutral_normalizer_parity() -> None:
    result = _result()
    context = normalization_context(profile_for_result(result), "paddle-structured")

    paddle_document = normalize_paddleocr_vl_result(result, context)
    neutral_document = normalize_neutral_result(result, context)

    assert dump_canonical_json(paddle_document) == dump_canonical_json(neutral_document)


def test_sanitized_label_contract_replays_into_canonical_ir() -> None:
    result = _label_contract_result()
    context = normalization_context(profile_for_result(result), "paddle-label-contract")

    document = normalize_neutral_result(result, context)
    provenance = {record.provenance_id: record for record in document.provenance}
    blocks_by_source = {
        provenance[block.provenance_ids[0]].original_object_id: block
        for block in document.pages[0].blocks
    }

    assert blocks_by_source["paddle:1:0"].block_type is BlockType.TITLE
    assert blocks_by_source["paddle:1:1"].block_type is BlockType.HEADING
    assert blocks_by_source["paddle:1:2"].block_type is BlockType.PARAGRAPH
    assert blocks_by_source["paddle:1:3"].block_type is BlockType.FIGURE
    assert blocks_by_source["paddle:1:5"].block_type is BlockType.EQUATION
    assert blocks_by_source["paddle:1:6"].block_type is BlockType.UNKNOWN
    assert blocks_by_source["paddle:1:7"].block_type is BlockType.TABLE
    assert blocks_by_source["paddle:1:12"].block_type is BlockType.UNKNOWN
    assert len(document.equations) == 1
    assert len(document.tables) == 1
    assert len(document.tables[0].cells) == 4
    assert len(document.figures) == 1
    assert document.figures[0].caption_block_ids == (
        blocks_by_source["paddle:1:4"].block_id,
    )
    assert document.tables[0].caption_block_ids == (
        blocks_by_source["paddle:1:8"].block_id,
    )
    assert blocks_by_source["paddle:1:13"].block_id not in document.figures[0].caption_block_ids
    assert all(block.provenance_ids for block in document.pages[0].blocks)
    assert all(record.original_object_id for record in provenance.values())

    in_flow = [
        block
        for block in document.pages[0].blocks
        if block.reading_order_status is ReadingOrderStatus.IN_FLOW
    ]
    assert [block.reading_order for block in in_flow] == list(range(len(in_flow)))
    assert blocks_by_source["paddle:1:9"].reading_order_status is ReadingOrderStatus.DECORATIVE
    assert blocks_by_source["paddle:1:10"].reading_order_status is ReadingOrderStatus.DECORATIVE
    assert blocks_by_source["paddle:1:11"].reading_order_status is ReadingOrderStatus.DECORATIVE

    repeated = normalize_neutral_result(result, context)
    assert dump_canonical_json(document) == dump_canonical_json(repeated)


def test_duplicate_or_missing_paddle_order_is_not_canonicalized_into_flow() -> None:
    result = map_paddleocr_vl_pages(
        [
            {
                "page_index": 0,
                "source_width": 100,
                "source_height": 100,
                "parsing_res_list": [
                    {
                        "block_id": 1,
                        "block_order": 5,
                        "block_label": "text",
                        "block_bbox": [1, 1, 40, 20],
                        "block_content": "First",
                    },
                    {
                        "block_id": 2,
                        "block_order": 5,
                        "block_label": "text",
                        "block_bbox": [1, 30, 40, 50],
                        "block_content": "Second",
                    },
                    {
                        "block_id": 3,
                        "block_order": None,
                        "block_label": "text",
                        "block_bbox": [1, 60, 40, 80],
                        "block_content": "Third",
                    },
                ],
            }
        ],
        descriptor=_descriptor(),
        run=_run(),
    )
    context = normalization_context(profile_for_result(result), "paddle-order-conflict")

    document = normalize_neutral_result(result, context)

    assert all(
        block.reading_order_status is ReadingOrderStatus.UNRESOLVED
        and block.reading_order is None
        for block in document.pages[0].blocks
    )

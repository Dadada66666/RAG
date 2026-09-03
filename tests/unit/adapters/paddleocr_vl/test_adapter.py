from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.parser_fixture import normalization_context, profile_for_result

from docparser.adapters.parsers.paddleocr_vl.adapter import PaddleOCRVLParserAdapter
from docparser.adapters.parsers.paddleocr_vl.mapping import map_paddleocr_vl_pages
from docparser.adapters.parsers.paddleocr_vl.runtime import PaddleOCRVLRuntimeError
from docparser.domain.parser_contract import ParserRun, RuntimeDevice
from docparser.ir.ids import ParserRunId
from docparser.ir.types import UtcTimestamp
from docparser.normalization import normalize_neutral_result


class _PrivatePaddleBlock:
    def __str__(self) -> str:
        return "################# label: table #################"


class _MappingLikePaddleResult(dict[str, object]):
    def __init__(self) -> None:
        super().__init__({"res": {"parsing_res_list": [_PrivatePaddleBlock()]}})
        self.json = {
            "res": {
                "input_path": "C:/private/timetable.pdf",
                "page_index": 0,
                "width": 1224,
                "height": 1584,
                "model_settings": {"use_layout_detection": True},
                "parsing_res_list": [
                    {
                        "block_id": 0,
                        "block_order": None,
                        "block_label": "table",
                        "block_bbox": [120, 240, 1100, 760],
                        "block_content": (
                            "<table><tr><td>A</td><td>B</td></tr>"
                            "<tr><td>1</td><td>2</td></tr></table>"
                        ),
                        "group_id": 0,
                    },
                    {
                        "block_id": 1,
                        "block_order": None,
                        "block_label": "number",
                        "block_bbox": [580, 1500, 640, 1540],
                        "block_content": "1",
                        "group_id": 1,
                    },
                ],
            }
        }

    def __iter__(self) -> Iterator[str]:
        return super().__iter__()


def _run() -> ParserRun:
    return ParserRun(
        parser_run_id=ParserRunId("prun_018bcfe5-6800-7000-8000-000000000091"),
        started_at=UtcTimestamp("2026-09-01T00:00:00Z"),
        ended_at=UtcTimestamp("2026-09-01T00:00:01Z"),
        requested_device=RuntimeDevice.CUDA,
        actual_device=RuntimeDevice.CUDA,
        determinism="BEST_EFFORT",
        runtime={"org.docparser.pipeline_version": "v1.6"},
    )


def test_official_result_json_preserves_structured_blocks_and_geometry() -> None:
    payload = PaddleOCRVLParserAdapter._sanitize_result(_MappingLikePaddleResult(), 9)

    assert payload["page_index"] == 0
    assert payload["source_width"] == 1224
    assert payload["source_height"] == 1584
    blocks = payload["parsing_res_list"]
    assert isinstance(blocks, list)
    assert all(isinstance(block, dict) for block in blocks)
    assert blocks[0]["block_label"] == "table"
    assert blocks[0]["block_content"].startswith("<table>")
    assert blocks[0]["block_bbox"] == [120, 240, 1100, 760]
    assert blocks[0]["block_id"] == 0
    assert blocks[0]["block_order"] is None
    assert "#################" not in str(blocks)


def test_official_result_json_maps_table_to_neutral_contract() -> None:
    adapter = PaddleOCRVLParserAdapter()
    payload = adapter._sanitize_result(_MappingLikePaddleResult(), 0)

    result = map_paddleocr_vl_pages([payload], descriptor=adapter.descriptor(), run=_run())

    assert result.pages[0].elements
    assert result.pages[0].tables
    assert result.pages[0].tables[0].row_count == 2
    assert result.pages[0].tables[0].column_count == 2
    assert len(result.pages[0].tables[0].cells) == 4
    assert result.descriptor.adapter_version == "0.1.1"

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "paddle-official-json"),
    )
    assert document.pages[0].blocks
    assert document.tables
    assert len(document.tables[0].cells) == 4


def test_raw_snapshot_retains_structured_blocks(tmp_path: Path) -> None:
    payload = PaddleOCRVLParserAdapter._sanitize_result(_MappingLikePaddleResult(), 0)

    PaddleOCRVLParserAdapter._write_raw_snapshot([payload], tmp_path)

    snapshot = json.loads((tmp_path / "paddleocr-vl-pages.json").read_text(encoding="utf-8"))
    blocks = snapshot["pages"][0]["parsing_res_list"]
    assert isinstance(blocks, list)
    assert all(isinstance(block, dict) for block in blocks)
    assert blocks[0]["block_content"].startswith("<table>")


def test_official_result_json_rejects_private_sdk_objects() -> None:
    result = _MappingLikePaddleResult()
    result.json["res"]["parsing_res_list"] = [_PrivatePaddleBlock()]

    with pytest.raises(PaddleOCRVLRuntimeError, match="non-JSON-safe"):
        PaddleOCRVLParserAdapter._sanitize_result(result, 0)

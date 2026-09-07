from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from tests.parser_fixture import normalization_context, profile_for_result
from tests.unit.adapters.paddleocr_vl.test_mapping import _descriptor, _run

from docparser.adapters.parsers.paddleocr_vl.mapping import map_paddleocr_vl_pages
from docparser.ir.enums import BlockType
from docparser.normalization import normalize_neutral_result


@pytest.mark.integration
def test_saved_paddle_payload_replays_without_parser_runtime() -> None:
    configured = os.environ.get("DOCPARSER_PADDLE_REPLAY_PAYLOAD")
    if configured is None:
        pytest.skip("set DOCPARSER_PADDLE_REPLAY_PAYLOAD to a saved raw payload")
    payload_path = Path(configured)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    result = map_paddleocr_vl_pages(payload["pages"], descriptor=_descriptor(), run=_run())
    context = normalization_context(profile_for_result(result), "paddle-real-payload-replay")
    document = normalize_neutral_result(result, context)

    blocks = tuple(block for page in document.pages for block in page.blocks)
    diagnostics = {
        "pages": document.page_count,
        "blocks": len(blocks),
        "tables": len(document.tables),
        "table_cells": sum(len(table.cells) for table in document.tables),
        "block_types": dict(sorted(Counter(block.block_type.value for block in blocks).items())),
        "unknown_text_chars": sum(
            len(block.text or "") for block in blocks if block.block_type is BlockType.UNKNOWN
        ),
        "warnings": list(result.warnings),
    }
    print(json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))

    assert document.pages
    assert blocks
    assert all(block.provenance_ids for block in blocks)

"""Opt-in local BGE tokenizer test: no network, GPU, PDF, holdout questions or chat API."""

import importlib
import os
from pathlib import Path

import pytest
from tests.unit.test_table_context import hit_row, long_table, setup

from docparser.retrieval.dense import _SentenceTransformerTokenizer


@pytest.mark.integration
def test_local_bge_table_source_offsets() -> None:
    if os.environ.get("DOCPARSER_RUN_TABLE_CONTEXT_SMOKE") != "1":
        pytest.skip("set DOCPARSER_RUN_TABLE_CONTEXT_SMOKE=1 and BGE_M3_MODEL_PATH on the server")
    path = Path(os.environ["BGE_M3_MODEL_PATH"])
    assert path.is_dir()
    transformers = importlib.import_module("transformers")
    native = transformers.AutoTokenizer.from_pretrained(
        str(path), local_files_only=True, use_fast=True
    )
    tokenizer = _SentenceTransformerTokenizer(native, "local-bge-offset-smoke")
    document = long_table(rowspan=3)
    builder = setup(document, tokenizer)
    source = builder.sources[str(document.tables[0].segments[0].block_id)]
    assert source.table_map is not None and source.table_map.alignment == "ALIGNED"
    context = hit_row(builder, document, 5)
    assert {row for item in context.evidence for row in item.row_indices} == {0, 1, 3, 4, 5}
    assert all(item.row_band_complete for item in context.evidence)
    for item in context.evidence:
        assert item.text == source.text[item.char_start : item.char_end]
    assert context.token_count == len(tokenizer.encode(context.text))

from __future__ import annotations

from tests.retrieval_factory import CharacterTokenizer, make_retrieval_document

from docparser.ir.enums import BlockType, ChunkType, TableCellHeaderRole
from docparser.ir.invariants import validate_document_invariants
from docparser.ir.models import DocumentIR
from docparser.retrieval import (
    FixedChunkConfig,
    StructureChunkConfig,
    fixed_token_chunks,
    structure_aware_chunks,
)


def test_fixed_chunks_use_only_resolved_retrieval_flow_and_exact_overlap() -> None:
    document = make_retrieval_document()
    tokenizer = CharacterTokenizer()
    config = FixedChunkConfig(target_tokens=32, overlap_tokens=8)
    all_chunks = fixed_token_chunks(document, tokenizer, config)
    chunks = tuple(chunk for chunk in all_chunks if chunk.embedding_eligible)

    assert chunks
    assert all_chunks[0].chunk_type is ChunkType.PARENT
    assert all(
        not chunk.embedding_eligible
        for chunk in all_chunks
        if chunk.chunk_type is ChunkType.PARENT
    )
    assert all(chunk.token_count <= 32 for chunk in chunks)
    assert all(BlockType.HEADER not in chunk.content_types for chunk in chunks)
    assert all(BlockType.FOOTER not in chunk.content_types for chunk in chunks)
    assert all(BlockType.UNKNOWN not in chunk.content_types for chunk in chunks)
    assert "unresolved multicolumn text" not in "".join(chunk.text for chunk in chunks)
    assert tokenizer.encode(chunks[0].text)[-8:] == tokenizer.encode(chunks[1].text)[:8]
    assert all(chunk.provenance_ids and chunk.bboxes for chunk in chunks)
    assert all_chunks == fixed_token_chunks(document, tokenizer, config)
    assert FixedChunkConfig() == FixedChunkConfig(target_tokens=512, overlap_tokens=64)
    payload = document.model_dump(mode="python")
    payload["chunks"] = all_chunks
    validate_document_invariants(DocumentIR.model_validate(payload))


def test_fixed_baseline_can_split_a_table_as_an_ordinary_token_stream() -> None:
    chunks = fixed_token_chunks(
        make_retrieval_document(),
        CharacterTokenizer(),
        FixedChunkConfig(target_tokens=18, overlap_tokens=0),
    )
    table_chunks = [
        chunk
        for chunk in chunks
        if chunk.embedding_eligible and BlockType.TABLE in chunk.content_types
    ]

    assert len(table_chunks) > 1
    assert all(chunk.chunk_type is ChunkType.CHILD for chunk in table_chunks)


def test_structure_chunks_respect_sections_and_keep_heading_context() -> None:
    document = make_retrieval_document()
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=80, hard_max_tokens=200),
    )

    assert chunks
    assert all(chunk.parent_section_id is not None for chunk in chunks)
    assert all(len(chunk.heading_path) == 1 for chunk in chunks)
    assert all(not ({"Revenue", "Risk"} <= set(chunk.heading_path)) for chunk in chunks)
    assert {chunk.parent_section_id for chunk in chunks} == {
        document.sections[0].section_id,
        document.sections[1].section_id,
    }
    assert all(chunk.provenance_ids and chunk.source_block_ids for chunk in chunks)
    payload = document.model_dump(mode="python")
    payload["chunks"] = chunks
    validate_document_invariants(DocumentIR.model_validate(payload))


def test_structure_table_is_atomic_under_limit_and_row_grouped_when_oversized() -> None:
    document = make_retrieval_document()
    tokenizer = CharacterTokenizer()
    atomic = structure_aware_chunks(
        document,
        tokenizer,
        StructureChunkConfig(target_tokens=500, hard_max_tokens=500),
    )
    atomic_tables = [chunk for chunk in atomic if chunk.chunk_type is ChunkType.TABLE]
    assert len(atomic_tables) == 1
    assert "Metric" in atomic_tables[0].text and "Margin" in atomic_tables[0].text

    config = StructureChunkConfig(target_tokens=55, hard_max_tokens=120)
    grouped = structure_aware_chunks(document, tokenizer, config)
    table_chunks = [chunk for chunk in grouped if chunk.chunk_type is ChunkType.TABLE]
    assert len(table_chunks) > 1
    assert all("Metric" in chunk.text and "Value" in chunk.text for chunk in table_chunks)
    assert sum("| Revenue | 120 |" in chunk.text for chunk in table_chunks) == 1
    assert sum("| Profit | 30 |" in chunk.text for chunk in table_chunks) == 1
    assert sum("| Margin | 20 |" in chunk.text for chunk in table_chunks) == 1
    assert all(chunk.metadata["repeated_header_rows"] == [0] for chunk in table_chunks)
    assert grouped == structure_aware_chunks(document, tokenizer, config)


def test_unknown_table_header_roles_are_not_repeated_as_column_headers() -> None:
    document = make_retrieval_document()
    table = document.tables[0]
    cells = tuple(
        cell.model_copy(
            update={"header_role": TableCellHeaderRole.UNKNOWN, "is_header": True}
        )
        if cell.row_index == 0
        else cell
        for cell in table.cells
    )
    payload = document.model_dump(mode="python")
    payload["tables"] = (table.model_copy(update={"cells": cells, "header_row_indices": ()}),)
    unknown = DocumentIR.model_validate(payload)

    chunks = structure_aware_chunks(
        unknown,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=55, hard_max_tokens=120),
    )
    table_chunks = [chunk for chunk in chunks if chunk.chunk_type is ChunkType.TABLE]

    assert sum("| Metric | Value |" in chunk.text for chunk in table_chunks) == 1
    assert all(chunk.metadata["repeated_header_rows"] == [] for chunk in table_chunks)

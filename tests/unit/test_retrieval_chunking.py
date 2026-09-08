from __future__ import annotations

from typing import cast

from tests.ir_factory import TEST_NAMESPACE
from tests.retrieval_factory import CharacterTokenizer, make_retrieval_document

from docparser.ir.chunks import Chunk
from docparser.ir.content import Equation
from docparser.ir.enums import BlockType, ChunkType, EquationFormat, TableCellHeaderRole
from docparser.ir.ids import BlockId, EquationId, TableSegmentId, generate_uuid5_id
from docparser.ir.invariants import validate_document_invariants
from docparser.ir.models import DocumentIR
from docparser.ir.tables import TableSegment
from docparser.retrieval import (
    FixedChunkConfig,
    StructureChunkConfig,
    fixed_token_chunks,
    structure_aware_chunks,
)


def _validated(document: DocumentIR) -> DocumentIR:
    return DocumentIR.model_validate(document.model_dump(mode="python"))


def _metadata_int_list(chunk: Chunk, key: str) -> list[int]:
    value = chunk.metadata[key]
    assert isinstance(value, list)
    assert all(isinstance(item, int) for item in value)
    return cast(list[int], value)


def _metadata_str_list(chunk: Chunk, key: str) -> list[str]:
    value = chunk.metadata[key]
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return [str(item) for item in value]


def _with_table_caption(document: DocumentIR, *, linked: bool) -> DocumentIR:
    page = document.pages[0]
    caption = page.blocks[2].model_copy(update={"block_type": BlockType.FIGURE_CAPTION})
    pages = (
        page.model_copy(update={"blocks": (*page.blocks[:2], caption, *page.blocks[3:])}),
        *document.pages[1:],
    )
    table = document.tables[0].model_copy(
        update={"caption_block_ids": (caption.block_id,) if linked else ()}
    )
    return _validated(document.model_copy(update={"pages": pages, "tables": (table,)}))


def _with_three_normal_units(document: DocumentIR) -> DocumentIR:
    page = document.pages[0]
    converted = page.blocks[3].model_copy(
        update={
            "block_type": BlockType.PARAGRAPH,
            "content_ref": None,
            "text": "Operating margin expanded.",
        }
    )
    third = converted.model_copy(
        update={
            "block_id": generate_uuid5_id(BlockId, TEST_NAMESPACE, "retrieval-third-unit"),
            "reading_order": 3,
            "text": "Cash flow remained strong.",
        }
    )
    pages = (
        page.model_copy(
            update={"blocks": (*page.blocks[:3], converted, third, *page.blocks[4:])}
        ),
        *document.pages[1:],
    )
    first_section = document.sections[0].model_copy(
        update={"content_block_ids": (*document.sections[0].content_block_ids, third.block_id)}
    )
    return _validated(
        document.model_copy(
            update={
                "pages": pages,
                "sections": (first_section, *document.sections[1:]),
                "tables": (),
            }
        )
    )


def _with_row_span(document: DocumentIR) -> DocumentIR:
    table = document.tables[0]
    cells = tuple(
        cell.model_copy(update={"row_span": 2})
        if cell.row_index == 1 and cell.column_index == 0
        else cell
        for cell in table.cells
        if not (cell.row_index == 2 and cell.column_index == 0)
    )
    return _validated(
        document.model_copy(update={"tables": (table.model_copy(update={"cells": cells}),)})
    )


def _with_multisegment_table(document: DocumentIR) -> DocumentIR:
    table = document.tables[0]
    first = table.segments[0]
    page_two = document.pages[1]
    continuation = page_two.blocks[1].model_copy(
        update={
            "block_type": BlockType.TABLE,
            "content_ref": table.table_id,
            "text": "table continuation",
        }
    )
    second_id = generate_uuid5_id(
        TableSegmentId, TEST_NAMESPACE, "retrieval-segment-page-two"
    )
    first = first.model_copy(
        update={"row_end_exclusive": 2, "continues_to_segment_id": second_id}
    )
    second = TableSegment(
        segment_id=second_id,
        page_number=2,
        bbox=continuation.bbox,
        block_id=continuation.block_id,
        row_start=2,
        row_end_exclusive=4,
        continued_from_segment_id=first.segment_id,
        continues_to_segment_id=None,
        provenance_ids=continuation.provenance_ids,
        extensions={},
    )
    cells = tuple(
        cell.model_copy(
            update={
                "page_number": 2,
                "provenance_ids": continuation.provenance_ids,
            }
        )
        if cell.row_index >= 2
        else cell
        for cell in table.cells
    )
    pages = (
        document.pages[0],
        page_two.model_copy(
            update={
                "blocks": (
                    page_two.blocks[0],
                    continuation,
                    *page_two.blocks[2:],
                )
            }
        ),
    )
    updated_table = table.model_copy(
        update={
            "segments": (first, second),
            "cells": cells,
            "provenance_ids": tuple(
                dict.fromkeys((*table.provenance_ids, *continuation.provenance_ids))
            ),
        }
    )
    return _validated(document.model_copy(update={"pages": pages, "tables": (updated_table,)}))


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


def test_fixed_baseline_snapshot_is_unchanged_by_structure_v2() -> None:
    chunks = fixed_token_chunks(
        make_retrieval_document(),
        CharacterTokenizer(),
        FixedChunkConfig(target_tokens=32, overlap_tokens=8),
    )

    assert [str(chunk.chunk_id) for chunk in chunks] == [
        "chk_90f0b6bc-f0e2-58c7-894b-f57da6cd5d32",
        "chk_79492230-3b90-5eee-a54c-6d28bcaca0eb",
        "chk_f1e9618f-1a5c-5b4f-9e5c-ec329422532e",
        "chk_f7659046-88cf-5329-be8f-ada8c1be7bb3",
        "chk_ed9581a5-4a7b-51a2-89d4-97afbbfa4315",
        "chk_78d99278-fd2f-54f5-9cf0-bb5ce60ea4da",
        "chk_cbb99bd8-2daa-5d0b-8be3-f3c9c25b3e5c",
        "chk_6669ce5a-5a26-5491-80cc-b4627141eb65",
    ]


def test_structure_chunks_respect_sections_and_keep_heading_context() -> None:
    document = make_retrieval_document()
    assert StructureChunkConfig().semantic_overlap_units == 1
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
    assert all(chunk.chunker_version == "ir-structure-aware@2.0.0" for chunk in chunks)
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
    assert "Columns:\nMetric | Value" in atomic_tables[0].text
    assert "Metric: Revenue\nValue: 120" in atomic_tables[0].text
    assert "Metric: Margin\nValue: 20" in atomic_tables[0].text

    config = StructureChunkConfig(target_tokens=110, hard_max_tokens=200)
    grouped = structure_aware_chunks(document, tokenizer, config)
    table_chunks = [chunk for chunk in grouped if chunk.chunk_type is ChunkType.TABLE]
    assert len(table_chunks) > 1
    assert all("Metric" in chunk.text and "Value" in chunk.text for chunk in table_chunks)
    assert sum("Metric: Revenue\nValue: 120" in chunk.text for chunk in table_chunks) == 1
    assert sum("Metric: Profit\nValue: 30" in chunk.text for chunk in table_chunks) == 1
    assert sum("Metric: Margin\nValue: 20" in chunk.text for chunk in table_chunks) == 1
    assert any(len(_metadata_int_list(chunk, "data_row_indices")) > 1 for chunk in table_chunks)
    assert all(chunk.metadata["repeated_header_rows"] == [0] for chunk in table_chunks)
    assert all(chunk.token_count <= config.hard_max_tokens for chunk in table_chunks)
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
    assert all(
        chunk.metadata["table_rendering"] == "COMPACT_LOGICAL_ROWS"
        for chunk in table_chunks
    )
    assert all("Columns:" not in chunk.text for chunk in table_chunks)


def test_explicit_table_caption_is_bound_and_not_an_independent_candidate() -> None:
    document = _with_table_caption(make_retrieval_document(), linked=True)
    caption_id = document.tables[0].caption_block_ids[0]
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=150, hard_max_tokens=300),
    )
    table_chunks = [chunk for chunk in chunks if chunk.chunk_type is ChunkType.TABLE]
    embedding_chunks = [chunk for chunk in chunks if chunk.embedding_eligible]

    assert len(table_chunks) == 2
    assert all(
        "Table: Revenue increased strongly in 2025." in chunk.text
        for chunk in table_chunks
    )
    assert all(
        chunk.metadata["caption_block_ids"] == [str(caption_id)]
        for chunk in table_chunks
    )
    assert all(
        str(caption_id) in _metadata_str_list(chunk, "context_source_block_ids")
        for chunk in table_chunks
    )
    assert all(
        caption_id not in chunk.source_block_ids
        for chunk in embedding_chunks
        if chunk.chunk_type is not ChunkType.TABLE
    )


def test_unlinked_caption_remains_independent_without_heuristic_binding() -> None:
    document = _with_table_caption(make_retrieval_document(), linked=False)
    caption_id = document.pages[0].blocks[2].block_id
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=500, hard_max_tokens=500),
    )
    table_chunk = next(chunk for chunk in chunks if chunk.chunk_type is ChunkType.TABLE)

    assert "Table: Revenue increased strongly in 2025." not in table_chunk.text
    assert any(
        caption_id in chunk.source_block_ids
        for chunk in chunks
        if chunk.embedding_eligible and chunk.chunk_type is ChunkType.CHILD
    )


def test_explicit_table_caption_is_bound_across_section_boundaries() -> None:
    document = make_retrieval_document()
    page = document.pages[1]
    caption = page.blocks[1].model_copy(update={"block_type": BlockType.FIGURE_CAPTION})
    pages = (
        document.pages[0],
        page.model_copy(update={"blocks": (page.blocks[0], caption, *page.blocks[2:])}),
    )
    table = document.tables[0].model_copy(update={"caption_block_ids": (caption.block_id,)})
    document = _validated(document.model_copy(update={"pages": pages, "tables": (table,)}))

    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=500, hard_max_tokens=500),
    )
    table_chunk = next(chunk for chunk in chunks if chunk.chunk_type is ChunkType.TABLE)

    assert "Table: Risk factors remain material." in table_chunk.text
    assert all(
        caption.block_id not in chunk.source_block_ids
        for chunk in chunks
        if chunk.embedding_eligible and chunk.chunk_type is ChunkType.CHILD
    )


def test_row_spans_form_indivisible_bands_without_mid_cell_split() -> None:
    document = _with_row_span(make_retrieval_document())
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=80, hard_max_tokens=200),
    )
    table_chunks = [chunk for chunk in chunks if chunk.chunk_type is ChunkType.TABLE]

    assert any(chunk.metadata["data_row_indices"] == [1, 2] for chunk in table_chunks)
    assert not any(chunk.metadata["data_row_indices"] in ([1], [2]) for chunk in table_chunks)
    assert sum("Revenue [rowspan=2 colspan=1]" in chunk.text for chunk in table_chunks) == 1


def test_multisegment_row_group_provenance_only_uses_intersecting_pages() -> None:
    document = _with_multisegment_table(make_retrieval_document())
    second_segment = document.tables[0].segments[1]
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=80, hard_max_tokens=200),
    )
    page_two_groups = [
        chunk
        for chunk in chunks
        if chunk.chunk_type is ChunkType.TABLE
        and min(_metadata_int_list(chunk, "data_row_indices")) >= 2
    ]

    assert page_two_groups
    assert all(chunk.page_start == chunk.page_end == 2 for chunk in page_two_groups)
    assert all(chunk.source_block_ids == (second_segment.block_id,) for chunk in page_two_groups)
    assert all({bbox.page_number for bbox in chunk.bboxes} == {2} for chunk in page_two_groups)
    assert all(chunk.bboxes[0].bbox == second_segment.bbox for chunk in page_two_groups)
    assert all(
        chunk.metadata["table_segment_ids"] == [str(second_segment.segment_id)]
        for chunk in page_two_groups
    )
    assert all(
        str(document.sections[0].heading_block_id)
        in _metadata_str_list(chunk, "context_source_block_ids")
        for chunk in page_two_groups
    )


def test_normal_units_pack_densely_and_overlap_one_complete_unit() -> None:
    document = _with_three_normal_units(make_retrieval_document())
    first_section = document.sections[0]
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(
            target_tokens=90,
            hard_max_tokens=120,
            semantic_overlap_units=1,
        ),
    )
    children = [
        chunk
        for chunk in chunks
        if chunk.chunk_type is ChunkType.CHILD
        and chunk.parent_section_id == first_section.section_id
    ]

    assert len(children) == 2
    assert children[0].metadata["semantic_unit_count"] == 2
    overlap_id = first_section.content_block_ids[1]
    assert children[1].metadata["overlap_source_block_ids"] == [str(overlap_id)]
    assert overlap_id in children[0].source_block_ids
    assert overlap_id in children[1].source_block_ids
    assert all(chunk.token_count <= 90 for chunk in children)
    assert all(
        chunk.metadata["overlap_source_block_ids"] == []
        for chunk in chunks
        if chunk.chunk_type is ChunkType.TABLE
    )


def test_semantic_overlap_never_crosses_sections() -> None:
    document = _with_three_normal_units(make_retrieval_document())
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=90, hard_max_tokens=120),
    )
    second_section_children = [
        chunk
        for chunk in chunks
        if chunk.chunk_type is ChunkType.CHILD
        and chunk.parent_section_id == document.sections[1].section_id
    ]

    assert second_section_children
    assert all(
        chunk.metadata["overlap_source_block_ids"] == []
        for chunk in second_section_children
    )


def test_protected_equation_is_not_used_as_semantic_overlap() -> None:
    document = _with_three_normal_units(make_retrieval_document())
    protected_id = document.sections[0].content_block_ids[-1]
    equation_id = generate_uuid5_id(EquationId, TEST_NAMESPACE, "retrieval-equation")
    page = document.pages[0]
    blocks = tuple(
        block.model_copy(
            update={"block_type": BlockType.EQUATION, "content_ref": equation_id}
        )
        if block.block_id == protected_id
        else block
        for block in page.blocks
    )
    protected_block = next(block for block in blocks if block.block_id == protected_id)
    equation = Equation(
        equation_id=equation_id,
        block_id=protected_id,
        text=protected_block.text or "",
        format=EquationFormat.PLAIN,
        label=None,
        provenance_ids=protected_block.provenance_ids,
        confidence=None,
        extensions={},
    )
    document = _validated(
        document.model_copy(
            update={
                "pages": (
                    page.model_copy(update={"blocks": blocks}),
                    *document.pages[1:],
                ),
                "equations": (equation,),
            }
        )
    )
    chunks = structure_aware_chunks(
        document,
        CharacterTokenizer(),
        StructureChunkConfig(target_tokens=90, hard_max_tokens=120),
    )
    equation_chunk = next(
        chunk
        for chunk in chunks
        if chunk.embedding_eligible and protected_id in chunk.source_block_ids
    )

    assert equation_chunk.source_block_ids == (protected_id,)
    assert equation_chunk.metadata["protected_unit"] == "EQUATION"
    assert equation_chunk.metadata["overlap_source_block_ids"] == []


def test_normal_block_splits_only_after_exceeding_hard_limit() -> None:
    def with_paragraph(text: str) -> tuple[DocumentIR, BlockId]:
        document = make_retrieval_document()
        page = document.pages[0]
        paragraph = page.blocks[2].model_copy(update={"text": text})
        pages = (
            page.model_copy(
                update={"blocks": (*page.blocks[:2], paragraph, *page.blocks[3:])}
            ),
            *document.pages[1:],
        )
        return _validated(document.model_copy(update={"pages": pages})), paragraph.block_id

    config = StructureChunkConfig(target_tokens=60, hard_max_tokens=120)
    below_hard, paragraph_id = with_paragraph("x" * 70)
    intact = [
        chunk
        for chunk in structure_aware_chunks(below_hard, CharacterTokenizer(), config)
        if chunk.embedding_eligible and paragraph_id in chunk.source_block_ids
    ]
    assert len(intact) == 1
    assert intact[0].token_count > config.target_tokens
    assert intact[0].token_count <= config.hard_max_tokens
    assert intact[0].metadata["oversized_text_block_split"] is False

    above_hard, paragraph_id = with_paragraph("x" * 150)
    splits = [
        chunk
        for chunk in structure_aware_chunks(above_hard, CharacterTokenizer(), config)
        if chunk.embedding_eligible and paragraph_id in chunk.source_block_ids
    ]
    assert len(splits) > 1
    assert all(chunk.metadata["oversized_text_block_split"] is True for chunk in splits)
    assert all(chunk.metadata["overlap_source_block_ids"] == [] for chunk in splits)
    assert all(chunk.token_count <= config.target_tokens for chunk in splits)


def test_non_embedding_parent_is_counted_without_one_oversized_encode() -> None:
    class GuardTokenizer(CharacterTokenizer):
        def encode(self, text: str) -> tuple[int, ...]:
            if len(text) > 150:
                raise AssertionError("non-embedding parent was encoded as one giant input")
            return super().encode(text)

    chunks = structure_aware_chunks(
        make_retrieval_document(),
        GuardTokenizer(),
        StructureChunkConfig(target_tokens=80, hard_max_tokens=150),
    )
    parents = [chunk for chunk in chunks if chunk.chunk_type is ChunkType.PARENT]

    assert any(parent.token_count > 150 for parent in parents)
    assert all(not parent.embedding_eligible for parent in parents)

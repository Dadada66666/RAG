"""M2 contracts: exact offsets, table conditions, budget, source fidelity and ranking parity."""

from collections.abc import Sequence
from pathlib import Path

import pytest
from tests.ir_factory import TEST_NAMESPACE
from tests.retrieval_factory import (
    CharacterTokenizer,
    FakeEmbeddingRuntime,
    make_retrieval_document,
)
from tests.unit.test_evidence_context import retrieved
from tests.unit.test_evidence_qa import CitingModel
from tests.unit.test_retrieval_chunking import _with_multisegment_table

from docparser.ir.enums import (
    BlockType,
    ChunkType,
    ReadingOrderStatus,
    RelationshipType,
    TableCellHeaderRole,
)
from docparser.ir.ids import RelationshipId, TableCellId, generate_uuid5_id
from docparser.ir.models import DocumentIR
from docparser.ir.relationships import Relationship
from docparser.retrieval.answering import answer_from_context
from docparser.retrieval.chunking import (
    FixedChunkConfig,
    StructureChunkConfig,
    Tokenizer,
    _render_table_row,
    fixed_token_chunks,
)
from docparser.retrieval.context import (
    ContextBuilder,
    ContextConfig,
    EvidenceContext,
    SourceSpan,
    prepare_sources,
)
from docparser.retrieval.dense import QueryRetrieval
from docparser.retrieval.index import build_evidence_index, load_evidence_index
from docparser.retrieval.table_context import SourceEncoding


class OffsetCharacters(CharacterTokenizer):
    def encode_with_offsets(self, text: str) -> SourceEncoding:
        return SourceEncoding(self.encode(text), tuple((i, i + 1) for i in range(len(text))))


class PairTokenizer(OffsetCharacters):
    """Non-compositional row tokenization, including tokens spanning newlines/Unicode."""

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(
            ord(text[i]) * 0x110000 + (ord(text[i + 1]) if i + 1 < len(text) else 0)
            for i in range(0, len(text), 2)
        )

    def decode(self, token_ids: Sequence[int]) -> str:
        return "".join(
            chr(value) for token in token_ids for value in divmod(token, 0x110000) if value
        )

    def encode_with_offsets(self, text: str) -> SourceEncoding:
        return SourceEncoding(
            self.encode(text), tuple((i, min(i + 2, len(text))) for i in range(0, len(text), 2))
        )


def long_table(*, unknown: bool = False, rowspan: int = 1) -> DocumentIR:
    document = make_retrieval_document()
    table = document.tables[0]
    cells = []
    for row in range(12):
        for column in range(2):
            if column == 0 and 3 < row < 3 + rowspan:
                continue
            role = (
                TableCellHeaderRole.COLUMN_HEADER
                if row < 2
                else TableCellHeaderRole.ROW_HEADER
                if column == 0
                else TableCellHeaderRole.NONE
            )
            if unknown and row < 2:
                role = TableCellHeaderRole.UNKNOWN
            value = (
                ("项目", "2025 USD million")
                if row == 0
                else ("公司", "Revenue\n净额")
                if row == 1
                else (f"企业 {row}", "120")
            )[column]
            cells.append(
                table.cells[0].model_copy(
                    update={
                        "cell_id": generate_uuid5_id(
                            TableCellId, TEST_NAMESPACE, "m2", str(row), str(column)
                        ),
                        "row_index": row,
                        "column_index": column,
                        "text": value,
                        "row_span": rowspan if row == 3 and column == 0 else 1,
                        "is_header": role != TableCellHeaderRole.NONE,
                        "header_role": role,
                    }
                )
            )
    table = table.model_copy(
        update={
            "cells": tuple(cells),
            "logical_row_count": 12,
            "header_row_indices": () if unknown else (0, 1),
            "segments": (table.segments[0].model_copy(update={"row_end_exclusive": 12}),),
        }
    )
    return DocumentIR.model_validate(document.model_copy(update={"tables": (table,)}).model_dump())


def setup(document: DocumentIR, tokenizer: Tokenizer | None = None) -> ContextBuilder:
    tokenizer = tokenizer or OffsetCharacters()
    chunks = fixed_token_chunks(
        document, tokenizer, FixedChunkConfig(target_tokens=32, overlap_tokens=4)
    )
    sources, _ = prepare_sources(document, tokenizer, chunks)
    return ContextBuilder(sources, tokenizer)


def from_spans(
    builder: ContextBuilder, document: DocumentIR, spans: tuple[SourceSpan, ...], **config: object
) -> EvidenceContext:
    chunk = next(
        chunk
        for chunk in fixed_token_chunks(document, builder.tokenizer)
        if chunk.embedding_eligible
    )
    settings = ContextConfig.model_validate(
        {
            "table_policy": "LOGICAL_ROWS",
            "max_tokens": 8000,
            "expand_source_tokens": 0,
            "include_related": False,
            **config,
        }
    )
    return builder.build(retrieved((chunk,)), {str(chunk.chunk_id): spans}, settings)


def hit_row(
    builder: ContextBuilder, document: DocumentIR, row: int, segment: int = 0, **config: object
) -> EvidenceContext:
    identifier = str(document.tables[0].segments[segment].block_id)
    mapping = builder.sources[identifier].table_map
    assert mapping is not None
    selected = next(value for value in mapping.rows if value.row_index == row)
    return from_spans(
        builder,
        document,
        (
            SourceSpan(
                source_id=identifier,
                token_start=selected.token_end - 3,
                token_end=selected.token_end - 2,
            ),
        ),
        **config,
    )


def starvation_case() -> tuple[
    ContextBuilder,
    QueryRetrieval,
    dict[str, tuple[SourceSpan, ...]],
    str,
    str,
]:
    """Six ranked direct hits where table restoration is much costlier than each hit."""
    document = long_table()
    page = document.pages[0]
    caption = page.blocks[2].model_copy(
        update={
            "block_type": BlockType.FIGURE_CAPTION,
            "text": "Table 17: Synthetic allocation matrix",
        }
    )
    fact = page.blocks[4].model_copy(update={"text": "Operation Zebra | Role Beta | Denied"})
    table = document.tables[0].model_copy(update={"caption_block_ids": (caption.block_id,)})
    document = document.model_copy(
        update={
            "tables": (table,),
            "pages": (
                page.model_copy(
                    update={"blocks": (*page.blocks[:2], caption, page.blocks[3], fact)}
                ),
                document.pages[1],
            ),
        }
    )
    builder = setup(document)
    chunks = tuple(
        chunk
        for chunk in fixed_token_chunks(
            document,
            builder.tokenizer,
            FixedChunkConfig(target_tokens=32, overlap_tokens=4),
        )
        if chunk.embedding_eligible
    )
    assert len(chunks) >= 6
    table_source_id = str(table.segments[0].block_id)
    fact_source_id = str(fact.block_id)
    mapping = builder.sources[table_source_id].table_map
    assert mapping is not None
    rows = [
        next(row for row in mapping.rows if row.row_index == index)
        for index in (2, 4, 6, 8, 10)
    ]
    spans: dict[str, tuple[SourceSpan, ...]] = {
        str(chunk.chunk_id): (
            SourceSpan(
                source_id=table_source_id,
                token_start=row.token_end - 3,
                token_end=row.token_end - 2,
            ),
        )
        for chunk, row in zip(chunks[:5], rows, strict=True)
    }
    spans[str(chunks[5].chunk_id)] = (
        SourceSpan(
            source_id=fact_source_id,
            token_start=0,
            token_end=len(builder.tokenizer.encode(builder.sources[fact_source_id].text)),
        ),
    )
    return builder, retrieved(chunks[:6]), spans, fact_source_id, str(caption.block_id)


@pytest.mark.parametrize("tokenizer", [OffsetCharacters(), PairTokenizer()])
def test_whole_source_offsets_match_multiline_unicode_repeated_values(tokenizer: Tokenizer) -> None:
    document = long_table()
    builder = setup(document, tokenizer)
    identifier = str(document.tables[0].segments[0].block_id)
    source = builder.sources[identifier]
    mapping = source.table_map
    assert mapping is not None and mapping.alignment == "ALIGNED"
    for row in mapping.rows:
        assert source.text[row.char_start : row.char_end] == _render_table_row(
            document.tables[0], row.row_index
        )
        assert row.token_start < row.token_end
    context = hit_row(builder, document, 8)
    assert {row for item in context.evidence for row in item.row_indices} == {0, 1, 8}
    assert "企业 8" in context.text and "企业 7" not in context.text
    assert "2025 USD million" in context.text and "Revenue\n净额" in context.text
    assert all(item.row_band_complete for item in context.evidence)
    for item in context.evidence:
        assert item.text == source.text[item.char_start : item.char_end]


def test_small_table_restored_with_no_cell_geometry_invented() -> None:
    document = make_retrieval_document()
    context = hit_row(setup(document), document, 2, expand_source_tokens=768)
    item = context.evidence[0]
    assert item.complete_source and item.row_indices == (0, 1, 2, 3)
    assert len(item.covered_cell_ids) == 8
    assert all(location.precision == "TABLE_REGION" for location in item.locations)


def test_rowspan_closure_and_overlapping_hits_keep_one_anchor_cell() -> None:
    document = long_table(rowspan=3)
    builder = setup(document)
    context = hit_row(builder, document, 5)
    assert {row for item in context.evidence for row in item.row_indices} == {0, 1, 3, 4, 5}
    assert context.text.count("企业 3 [rowspan=3 colspan=1]") == 1
    identifier = str(document.tables[0].segments[0].block_id)
    mapping = builder.sources[identifier].table_map
    assert mapping is not None
    spans = tuple(
        SourceSpan(source_id=identifier, token_start=row.token_end - 3, token_end=row.token_end - 1)
        for row in mapping.rows
        if row.row_index in {4, 5, 10}
    )
    combined = from_spans(builder, document, spans, max_excerpt_tokens=1)
    all_rows = [row for item in combined.evidence for row in item.row_indices]
    assert set(all_rows) == {0, 1, 3, 4, 5, 10} and len(all_rows) == len(set(all_rows))
    assert combined.text.count("企业 3 [rowspan=3 colspan=1]") == 1


def test_unknown_axes_do_not_become_column_headers_or_guess_neighbors() -> None:
    document = long_table(unknown=True)
    pages = list(document.pages)
    blocks = list(pages[0].blocks)
    blocks[3] = blocks[3].model_copy(
        update={"reading_order": None, "reading_order_status": ReadingOrderStatus.UNRESOLVED}
    )
    pages[0] = pages[0].model_copy(update={"blocks": tuple(blocks)})
    document = document.model_copy(update={"pages": tuple(pages)})
    context = hit_row(setup(document), document, 8)
    assert len(context.evidence) == 1 and context.evidence[0].row_indices == (8,)
    assert "HEADER_ROLES_UNKNOWN" in context.text and "NO_EXPLICIT_COLUMN_HEADERS" in context.text
    assert "2025" not in context.evidence[0].text


@pytest.mark.parametrize("linked", [True, False])
def test_cross_page_header_only_via_explicit_continuation_and_citation_regions(
    linked: bool,
) -> None:
    document = _with_multisegment_table(make_retrieval_document())
    if not linked:
        table = document.tables[0]
        document = document.model_copy(
            update={
                "tables": (
                    table.model_copy(
                        update={
                            "segments": tuple(
                                segment.model_copy(
                                    update={
                                        "continued_from_segment_id": None,
                                        "continues_to_segment_id": None,
                                    }
                                )
                                for segment in table.segments
                            )
                        }
                    ),
                )
            }
        )
    context = hit_row(setup(document), document, 2, segment=1)
    assert {loc.page_number for item in context.evidence for loc in item.locations} == (
        {1, 2} if linked else {2}
    )
    assert ("TABLE_ROWS_UNAVAILABLE" in context.text) is (not linked)
    body = next(item for item in context.evidence if 2 in item.row_indices)
    assert "Profit" in body.text
    answer = answer_from_context("Profit?", context, CitingModel(body.evidence_id, "Profit"))
    assert answer.status == "ANSWERED"
    citation = answer.claims[0].citations[0]
    assert citation.row_indices == (2,)
    assert {location.page_number for location in citation.locations} == {2}
    assert set(citation.covered_cell_ids) == set(body.covered_cell_ids)


def test_missing_offsets_are_explicit_and_do_not_claim_cell_precision() -> None:
    document = long_table()
    builder = setup(document, CharacterTokenizer())
    identifier = str(document.tables[0].segments[0].block_id)
    context = from_spans(
        builder, document, (SourceSpan(source_id=identifier, token_start=150, token_end=156),)
    )
    assert "TABLE_ALIGNMENT_OFFSETS_UNAVAILABLE" in context.text
    assert not context.evidence[0].covered_cell_ids
    assert context.evidence[0].row_band_complete is False


def test_rowspan_across_explicit_continuation_keeps_anchor_and_both_pages() -> None:
    document = _with_multisegment_table(make_retrieval_document())
    table = document.tables[0]
    cells = tuple(
        cell.model_copy(update={"row_span": 2})
        if cell.row_index == 1 and cell.column_index == 0
        else cell
        for cell in table.cells
        if not (cell.row_index == 2 and cell.column_index == 0)
    )
    document = DocumentIR.model_validate(
        document.model_copy(
            update={"tables": (table.model_copy(update={"cells": cells}),)}
        ).model_dump()
    )
    before = document.model_dump_json()
    context = hit_row(setup(document), document, 2, segment=1)
    assert before == document.model_dump_json()
    assert {row for item in context.evidence for row in item.row_indices} == {0, 1, 2}
    assert context.text.count("Revenue [rowspan=2 colspan=1]") == 1
    assert {loc.page_number for item in context.evidence for loc in item.locations} == {1, 2}
    assert all(item.row_band_complete and item.segment_ids for item in context.evidence)


def test_missing_relation_source_discloses_missing_condition() -> None:
    document = long_table()
    builder = setup(document)
    identifier = str(document.tables[0].segments[0].block_id)
    builder.sources[identifier] = builder.sources[identifier].model_copy(
        update={"required_context_source_ids": ("missing-caption",)}
    )
    context = hit_row(builder, document, 8)
    assert "TABLE_REQUIRED_CONTEXT_UNAVAILABLE" in context.text


def test_old_index_without_maps_remains_usable_and_explicit() -> None:
    document = long_table()
    builder = setup(document)
    identifier = str(document.tables[0].segments[0].block_id)
    sources = {
        key: source.model_copy(update={"table_map": None})
        for key, source in builder.sources.items()
    }
    legacy = ContextBuilder(sources, builder.tokenizer)
    context = from_spans(
        legacy, document, (SourceSpan(source_id=identifier, token_start=150, token_end=156),)
    )
    assert "TABLE_ALIGNMENT_MAP_UNAVAILABLE" in context.text
    assert context.evidence[0].row_band_complete is False


@pytest.mark.parametrize("bad_ids", [True, False])
def test_invalid_offset_contract_is_detected(bad_ids: bool) -> None:
    class BrokenOffsets(OffsetCharacters):
        def encode_with_offsets(self, text: str) -> SourceEncoding:
            valid = super().encode_with_offsets(text)
            return SourceEncoding(
                (999,) if bad_ids else valid.token_ids,
                valid.offsets if bad_ids else ((0, len(text) + 1),) * len(valid.token_ids),
            )

    document = long_table()
    builder = setup(document, BrokenOffsets())
    source = builder.sources[str(document.tables[0].segments[0].block_id)]
    assert source.table_map is not None
    assert source.table_map.alignment == ("TOKEN_MISMATCH" if bad_ids else "INVALID_OFFSETS")
    assert not source.table_map.rows


def test_oversized_rowspan_budget_falls_back_explicitly_or_omits() -> None:
    document = long_table(rowspan=8)
    builder = setup(document)
    full = hit_row(builder, document, 8)
    budget = full.token_count - 1
    context = hit_row(builder, document, 8, max_tokens=budget)
    assert context.token_count <= budget
    assert "TABLE_RESTORATION_BUDGET_EXCEEDED" in context.warnings
    assert context.evidence and context.evidence[0].row_band_complete is False
    assert not context.evidence[0].covered_cell_ids
    empty = hit_row(builder, document, 8, max_tokens=1)
    assert not empty.evidence and empty.omitted_source_ids


def test_direct_core_reservation_prevents_table_enrichment_starvation() -> None:
    builder, retrieval, spans, fact_source_id, _ = starvation_case()
    direct = builder.build(
        retrieval,
        spans,
        ContextConfig(
            max_tokens=10000,
            max_excerpt_tokens=64,
            expand_source_tokens=0,
            include_related=False,
        ),
    )
    context = builder.build(
        retrieval,
        spans,
        ContextConfig(
            max_tokens=direct.token_count,
            max_excerpt_tokens=64,
            expand_source_tokens=0,
            include_related=False,
            table_policy="LOGICAL_ROWS",
            caption_context=True,
        ),
    )
    fact = next(item for item in context.evidence if item.source_id == fact_source_id)
    assert fact.text == "Operation Zebra | Role Beta | Denied"
    assert fact.retrieval_rank == 6
    assert context.token_count <= direct.token_count
    assert "TABLE_RESTORATION_BUDGET_EXCEEDED" in context.warnings


def test_direct_core_budget_impossibility_stops_by_rank_deterministically() -> None:
    builder, retrieval, spans, _, _ = starvation_case()
    first_hit = retrieval.model_copy(update={"hits": retrieval.hits[:1]})
    first_spans = {str(first_hit.hits[0].chunk_id): spans[str(first_hit.hits[0].chunk_id)]}
    first = builder.build(
        first_hit,
        first_spans,
        ContextConfig(
            max_tokens=10000,
            max_excerpt_tokens=64,
            expand_source_tokens=0,
            include_related=False,
        ),
    )
    config = ContextConfig(
        max_tokens=first.token_count,
        max_excerpt_tokens=64,
        expand_source_tokens=0,
        include_related=False,
        table_policy="LOGICAL_ROWS",
        caption_context=True,
    )
    actual = builder.build(retrieval, spans, config)
    assert actual.token_count <= config.max_tokens
    assert actual.evidence
    assert {item.retrieval_rank for item in actual.evidence} == {1}
    assert actual == builder.build(retrieval, spans, config)


def test_direct_reservation_does_not_disable_table_or_caption_enrichment() -> None:
    builder, retrieval, spans, fact_source_id, caption_source_id = starvation_case()
    context = builder.build(
        retrieval,
        spans,
        ContextConfig(
            max_tokens=10000,
            max_excerpt_tokens=64,
            expand_source_tokens=0,
            include_related=False,
            table_policy="LOGICAL_ROWS",
            caption_context=True,
        ),
    )
    assert any(item.row_indices for item in context.evidence)
    assert any(item.source_id == caption_source_id for item in context.evidence)
    assert any(item.source_id == fact_source_id for item in context.evidence)


def test_context_build_does_not_mutate_retrieval_hits() -> None:
    builder, retrieval, spans, _, _ = starvation_case()
    before = retrieval.model_dump_json()
    builder.build(
        retrieval,
        spans,
        ContextConfig(table_policy="LOGICAL_ROWS", caption_context=True),
    )
    assert retrieval.model_dump_json() == before


def test_starvation_regression_output_is_deterministic() -> None:
    builder, retrieval, spans, _, _ = starvation_case()
    config = ContextConfig(
        max_tokens=700,
        max_excerpt_tokens=64,
        expand_source_tokens=0,
        include_related=False,
        table_policy="LOGICAL_ROWS",
        caption_context=True,
    )
    first = builder.build(retrieval, spans, config)
    second = builder.build(retrieval, spans, config)
    assert first.text == second.text
    assert first.evidence == second.evidence
    assert first.warnings == second.warnings
    assert first.omitted_source_ids == second.omitted_source_ids


def test_explicit_caption_and_footnote_are_required_before_optional_heading() -> None:
    document = long_table()
    page = document.pages[0]
    caption = page.blocks[2].model_copy(
        update={
            "block_type": BlockType.FIGURE_CAPTION,
            "text": "Table 1: Annual revenue (USD million)",
        }
    )
    footnote = page.blocks[4].model_copy(
        update={"block_type": BlockType.FOOTNOTE, "text": "* Excludes discontinued operations."}
    )
    table = document.tables[0].model_copy(update={"caption_block_ids": (caption.block_id,)})
    relation = Relationship(
        relationship_id=generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "m2-footnote"),
        type=RelationshipType.FOOTNOTE_OF,
        source_id=footnote.block_id,
        target_id=table.table_id,
        confidence=None,
        provenance_ids=footnote.provenance_ids,
        metadata={},
    )
    document = document.model_copy(
        update={
            "tables": (table,),
            "relationships": (relation,),
            "pages": (
                page.model_copy(
                    update={"blocks": (*page.blocks[:2], caption, page.blocks[3], footnote)}
                ),
                document.pages[1],
            ),
        }
    )
    context = hit_row(setup(document), document, 8)
    assert caption.text is not None and caption.text in context.text
    assert footnote.text is not None and footnote.text in context.text
    assert {item.source_id for item in context.evidence} == {
        str(table.segments[0].block_id),
        str(caption.block_id),
        str(footnote.block_id),
    }
    assert context.config.include_related is False  # Essential conditions are still included.
    builder = setup(document)
    identifier = str(table.segments[0].block_id)
    mapping = builder.sources[identifier].table_map
    assert mapping is not None
    row = next(row for row in mapping.rows if row.row_index == 8)
    overlapping = from_spans(
        builder,
        document,
        (
            SourceSpan(source_id=str(caption.block_id), token_start=0, token_end=5),
            SourceSpan(
                source_id=identifier, token_start=row.token_end - 3, token_end=row.token_end - 1
            ),
        ),
    )
    captions = [item for item in overlapping.evidence if item.source_id == str(caption.block_id)]
    assert len(captions) == 1 and captions[0].complete_source


def test_index_roundtrip_context_determinism_and_fixed_embedding_parity(tmp_path: Path) -> None:
    document = long_table()
    old_runtime = FakeEmbeddingRuntime()
    runtime = FakeEmbeddingRuntime()
    runtime._tokenizer = OffsetCharacters()
    old = build_evidence_index((document,), old_runtime, tmp_path / "m1")
    new = build_evidence_index((document,), runtime, tmp_path / "m2")
    assert old_runtime.calls == runtime.calls
    assert old.entries == new.entries
    assert old.manifest.table_alignment_counts == {"OFFSETS_UNAVAILABLE": 1}
    assert new.manifest.table_alignment_counts == {"ALIGNED": 1}
    assert (old.vectors == new.vectors).all()
    loaded = load_evidence_index(tmp_path / "m2")
    assert loaded.sources == new.sources
    session = loaded.session(runtime)
    retrieval = session.retrieve("Revenue?")
    assert old.session(old_runtime).retrieve("Revenue?") == retrieval
    baseline = session.builder.build(retrieval, session.spans)
    assert old.session(old_runtime).builder.build(retrieval, session.spans).text == baseline.text
    config = ContextConfig(table_policy="LOGICAL_ROWS")
    first = session.builder.build(retrieval, session.spans, config)
    assert first == session.builder.build(retrieval, session.spans, config)
    assert first.token_count <= config.max_tokens
    assert any(item.row_indices for item in first.evidence)


def test_structure_index_embeds_semantic_tables_but_context_uses_source_rows(
    tmp_path: Path,
) -> None:
    document = long_table()
    runtime = FakeEmbeddingRuntime()
    runtime._tokenizer = OffsetCharacters()
    index = build_evidence_index(
        (document,),
        runtime,
        tmp_path / "structure",
        StructureChunkConfig(target_tokens=180, hard_max_tokens=500),
        chunking_policy="STRUCTURE",
    )

    assert index.manifest.version == "structure-evidence-index@1.0.0"
    assert index.manifest.chunking_policy == "STRUCTURE"
    assert index.manifest.chunker_version == "ir-structure-aware@2.4.0"
    table_entries = [
        entry for entry in index.entries if entry.chunk.chunk_type is ChunkType.TABLE
    ]
    assert len(table_entries) > 1
    assert all("Columns:" in entry.chunk.text for entry in table_entries)
    for entry in table_entries:
        raw_rows = entry.chunk.metadata["data_row_indices"]
        assert isinstance(raw_rows, list)
        expected_rows = {
            row for row in raw_rows if isinstance(row, int) and not isinstance(row, bool)
        }
        assert entry.source_spans
        for span in entry.source_spans:
            source = index.sources[span.source_id]
            assert source.table_map is not None
            mapped_rows = {
                row.row_index
                for row in source.table_map.rows
                if row.token_start == span.token_start and row.token_end == span.token_end
            }
            assert mapped_rows and mapped_rows <= expected_rows

    selected = table_entries[-1]
    context = index.session(runtime).context(
        retrieved((selected.chunk,)),
        ContextConfig(
            max_tokens=2000,
            max_excerpt_tokens=512,
            expand_source_tokens=0,
            include_related=False,
        ),
    )
    assert context.evidence
    assert "Columns:" not in context.text
    assert any("|" in item.text for item in context.evidence)
    assert all(item.source_id in index.sources for item in context.evidence)


def test_merged_column_header_remains_an_anchor_with_original_span() -> None:
    document = long_table()
    table = document.tables[0]
    cells = tuple(
        cell.model_copy(update={"column_span": 2, "text": "2025 Revenue / USD million"})
        if cell.row_index == 0 and cell.column_index == 0
        else cell
        for cell in table.cells
        if not (cell.row_index == 0 and cell.column_index == 1)
    )
    document = DocumentIR.model_validate(
        document.model_copy(
            update={"tables": (table.model_copy(update={"cells": cells}),)}
        ).model_dump()
    )
    context = hit_row(setup(document), document, 8)
    assert context.text.count("2025 Revenue / USD million [rowspan=1 colspan=2]") == 1
    assert {row for item in context.evidence for row in item.row_indices} == {0, 1, 8}

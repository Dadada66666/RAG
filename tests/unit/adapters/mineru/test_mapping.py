from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from tests.parser_fixture import normalization_context, profile_for_result
from tests.retrieval_factory import CharacterTokenizer

from docparser.adapters.parsers.mineru.mapping import map_mineru_middle
from docparser.domain.parser_contract import (
    ParserCapability,
    ParserDescriptor,
    ParseResult,
    ParserRun,
    ParseScopeKind,
    RuntimeDevice,
)
from docparser.ir.enums import (
    BlockType,
    ReadingOrderStatus,
    RelationshipType,
    TableCellHeaderRole,
)
from docparser.ir.ids import ParserRunId
from docparser.ir.serialization import dump_canonical_json
from docparser.ir.types import UtcTimestamp
from docparser.normalization import normalize_neutral_result
from docparser.retrieval import FixedChunkConfig, fixed_token_chunks


def _descriptor() -> ParserDescriptor:
    return ParserDescriptor(
        parser_name="mineru",
        parser_version="3.4.5",
        adapter_id="org.docparser.adapter.mineru",
        adapter_version="0.1.0",
        profile="mineru-3.4.5-hybrid-high-auto",
        capabilities=(
            ParserCapability.OCR,
            ParserCapability.TABLE,
            ParserCapability.FORMULA,
            ParserCapability.FIGURE,
            ParserCapability.LAYOUT,
            ParserCapability.READING_ORDER,
        ),
        supported_scopes=(ParseScopeKind.DOCUMENT,),
        model_identifiers=("MinerU-3.4.5-hybrid-high-auto@local",),
    )


def _run() -> ParserRun:
    return ParserRun(
        parser_run_id=ParserRunId("prun_018bcfe5-6800-7000-8000-0000000000a1"),
        started_at=UtcTimestamp("2026-09-01T00:00:00Z"),
        ended_at=UtcTimestamp("2026-09-01T00:00:01Z"),
        requested_device=RuntimeDevice.AUTO,
        actual_device=RuntimeDevice.CUDA,
        determinism="BEST_EFFORT",
        runtime={"org.docparser.profile": "mineru-3.4.5-hybrid-high-auto"},
    )


def _payload(name: str) -> dict[str, object]:
    value = json.loads(Path(f"tests/fixtures/mineru/{name}.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _result(name: str) -> ParseResult:
    return map_mineru_middle(_payload(name), descriptor=_descriptor(), run=_run())


def test_text_title_inline_equation_and_span_geometry_survive_normalization() -> None:
    result = _result("text-figure")
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-text-figure"),
    )
    title, paragraph = document.pages[0].blocks[:2]
    provenance = {record.provenance_id: record for record in document.provenance}

    assert title.block_type is BlockType.TITLE
    assert title.text == "Hybrid E=mc^2"
    assert [(span.start, span.end) for span in title.text_spans] == [(0, 7), (7, 13)]
    assert title.text_spans[1].bbox is not None
    assert title.text_spans[1].bbox.root == (145.0, 40.0, 240.0, 90.0)
    span_provenance = provenance[title.text_spans[1].provenance_ids[0]]
    assert span_provenance.char_range is not None
    assert span_provenance.char_range.root == (7, 13)
    assert paragraph.text == "First line\nSecond line"
    assert [block.reading_order for block in document.pages[0].blocks[:4]] == [0, 1, 2, 3]
    assert all(
        block.reading_order_status is ReadingOrderStatus.DECORATIVE
        for block in document.pages[0].blocks[-3:]
    )
    parser_metadata = title.extensions["org.docparser.parser_metadata"]
    assert isinstance(parser_metadata, dict)
    assert parser_metadata["org.mineru.heading_level"] == 1


def test_generated_visual_analysis_is_auxiliary_not_figure_or_fixed_text() -> None:
    result = _result("text-figure")
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-visual-analysis"),
    )
    figure_block = next(
        block for block in document.pages[0].blocks if block.block_type is BlockType.FIGURE
    )
    metadata = figure_block.extensions["org.docparser.parser_metadata"]
    assert isinstance(metadata, dict)

    assert figure_block.text is None
    assert "mermaid" in str(metadata["org.mineru.generated_visual_analysis"])
    assert document.figures[0].caption_block_ids
    assert any(relation.type is RelationshipType.CAPTION_OF for relation in document.relationships)
    chunks = fixed_token_chunks(document, CharacterTokenizer(), FixedChunkConfig())
    assert all("mermaid" not in chunk.text for chunk in chunks)


def test_cross_page_semantic_alignment_builds_one_table_with_precise_segments() -> None:
    result = _result("table-cross-page")
    first, second = result.pages[0].tables[0], result.pages[1].tables[0]
    assert first.continuation_to_source_object_id == second.source_object_id
    assert second.continuation_from_source_object_id == first.source_object_id

    context = normalization_context(profile_for_result(result), "mineru-cross-page-table")
    document = normalize_neutral_result(result, context)
    table = document.tables[0]
    provenance = {record.provenance_id: record for record in document.provenance}

    assert len(document.tables) == 1
    assert table.logical_row_count == 6
    assert table.logical_column_count == 2
    assert len(table.segments) == 2
    assert table.segments[0].continues_to_segment_id == table.segments[1].segment_id
    assert table.segments[1].continued_from_segment_id == table.segments[0].segment_id
    segment_ranges = [
        (segment.page_number, segment.row_start, segment.row_end_exclusive)
        for segment in table.segments
    ]
    assert segment_ranges == [
        (1, 0, 3),
        (2, 3, 6),
    ]
    assert {cell.page_number for cell in table.cells} == {1, 2}
    for cell in table.cells:
        assert provenance[cell.provenance_ids[0]].page_number == cell.page_number
    assert table.header_row_indices == ()
    assert any(cell.header_role is TableCellHeaderRole.UNKNOWN for cell in table.cells)


def test_failed_cross_page_alignment_keeps_tables_independent_with_warning() -> None:
    payload = _payload("table-cross-page")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    para_table = cast(list[dict[str, Any]], pages[0]["para_blocks"])[0]
    body = cast(list[dict[str, Any]], para_table["blocks"])[1]
    line = cast(list[dict[str, Any]], body["lines"])[0]
    span = cast(list[dict[str, Any]], line["spans"])[0]
    span["html"] = (
        "<table>"
        "<tr><td>Different</td><td>1</td></tr>"
        "<tr><td>Different</td><td>2</td></tr>"
        "<tr><td>Different</td><td>3</td></tr>"
        "<tr><td>Different</td><td>4</td></tr>"
        "</table>"
    )

    result = map_mineru_middle(payload, descriptor=_descriptor(), run=_run())
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-unaligned-table"),
    )

    assert len(document.tables) == 2
    assert all(len(table.segments) == 1 for table in document.tables)
    assert result.warnings == ("page 1: MinerU merged table has no exact local start",)


def test_table_caption_and_footnote_create_explicit_canonical_relationships() -> None:
    result = _result("table-cross-page")
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-table-relations"),
    )
    table = document.tables[0]
    caption = next(
        block for block in document.pages[0].blocks if block.block_type is BlockType.FIGURE_CAPTION
    )
    footnote = next(
        block for block in document.pages[0].blocks if block.block_type is BlockType.FOOTNOTE
    )
    relationships = {relation.type: relation for relation in document.relationships}

    assert table.caption_block_ids == (caption.block_id,)
    assert relationships[RelationshipType.CAPTION_OF].source_id == caption.block_id
    assert relationships[RelationshipType.CAPTION_OF].target_id == table.table_id
    assert relationships[RelationshipType.FOOTNOTE_OF].source_id == footnote.block_id
    assert relationships[RelationshipType.FOOTNOTE_OF].target_id == table.table_id
    assert caption.relationship_ids == (relationships[RelationshipType.CAPTION_OF].relationship_id,)
    assert footnote.relationship_ids == (
        relationships[RelationshipType.FOOTNOTE_OF].relationship_id,
    )


def test_multiple_table_identities_remain_visible_but_are_not_bound() -> None:
    result = _result("ambiguous-tables")
    assert len(result.pages[0].tables) == 2
    assert all(not table.caption_source_object_ids for table in result.pages[0].tables)
    assert (
        len(
            [
                element
                for element in result.pages[0].elements
                if element.element_type.value == "FIGURE_CAPTION"
            ]
        )
        == 4
    )

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-ambiguous-captions"),
    )
    assert all(not table.caption_block_ids for table in document.tables)
    assert not any(
        relationship.type is RelationshipType.CAPTION_OF for relationship in document.relationships
    )
    for table in document.tables:
        metadata = table.extensions["org.docparser.parser_metadata"]
        assert isinstance(metadata, dict)
        assert metadata["org.docparser.structural_ambiguity"] == ("MULTIPLE_TABLE_IDENTITIES")


def test_mineru_mapping_and_normalization_are_deterministic() -> None:
    result = _result("table-cross-page")
    context = normalization_context(profile_for_result(result), "mineru-deterministic")
    first = normalize_neutral_result(result, context)
    second = normalize_neutral_result(result, context)

    assert dump_canonical_json(first) == dump_canonical_json(second)

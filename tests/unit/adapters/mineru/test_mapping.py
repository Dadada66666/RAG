from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from tests.parser_fixture import normalization_context, profile_for_result
from tests.retrieval_factory import CharacterTokenizer

from docparser.adapters.parsers.mineru.mapping import map_mineru_middle
from docparser.adapters.parsers.mineru.options import ADAPTER_VERSION
from docparser.domain.parser_contract import (
    ExtractedElementType,
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
        adapter_version=ADAPTER_VERSION,
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


def test_list_composite_accepts_semantic_and_physical_text_children() -> None:
    result = _result("list-children")
    _, list_block, semantic_item, physical_text_item = result.pages[0].elements

    assert list_block.element_type is ExtractedElementType.LIST
    assert semantic_item.element_type is ExtractedElementType.LIST_ITEM
    assert physical_text_item.element_type is ExtractedElementType.LIST_ITEM
    assert semantic_item.parent_source_object_id == list_block.source_object_id
    assert physical_text_item.parent_source_object_id == list_block.source_object_id
    assert physical_text_item.text == "Beta item with x+y"
    assert [(span.start, span.end) for span in physical_text_item.text_spans] == [
        (0, 15),
        (15, 18),
    ]
    source_bbox = physical_text_item.text_spans[1].bbox
    assert source_bbox is not None
    assert (source_bbox.x0, source_bbox.y0, source_bbox.x1, source_bbox.y1) == (
        215.0,
        155.0,
        260.0,
        200.0,
    )
    assert semantic_item.metadata["org.mineru.block_type"] == "list_item"
    assert physical_text_item.metadata["org.mineru.block_type"] == "text"
    assert [element.reading_order for element in result.pages[0].elements] == [0, 1, 2, 3]

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-list-children"),
    )
    _, canonical_list, canonical_semantic_item, canonical_physical_item = document.pages[0].blocks
    assert canonical_list.block_type is BlockType.LIST
    assert canonical_semantic_item.block_type is BlockType.LIST_ITEM
    assert canonical_physical_item.block_type is BlockType.LIST_ITEM
    assert canonical_semantic_item.parent_block_id == canonical_list.block_id
    assert canonical_physical_item.parent_block_id == canonical_list.block_id
    assert canonical_physical_item.text == "Beta item with x+y"
    assert canonical_physical_item.text_spans[1].bbox is not None
    assert canonical_physical_item.text_spans[1].bbox.root == (215.0, 155.0, 260.0, 200.0)
    assert canonical_physical_item.provenance_ids
    assert canonical_physical_item.text_spans[1].provenance_ids
    parser_metadata = canonical_physical_item.extensions["org.docparser.parser_metadata"]
    assert isinstance(parser_metadata, dict)
    assert parser_metadata["org.mineru.block_type"] == "text"
    assert all(
        block.reading_order_status is ReadingOrderStatus.IN_FLOW
        for block in document.pages[0].blocks
    )

    chunks = fixed_token_chunks(document, CharacterTokenizer(), FixedChunkConfig())
    retrieval_text = "\n".join(chunk.text for chunk in chunks)
    assert "Alpha item" in retrieval_text
    assert "Beta item with x+y" in retrieval_text


def test_list_composite_rejects_unverified_child_type() -> None:
    payload = _payload("list-children")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    list_block = cast(dict[str, Any], pages[0]["preproc_blocks"][1])
    children = cast(list[dict[str, Any]], list_block["blocks"])
    children[1]["type"] = "future_list_child"

    with pytest.raises(
        ValueError,
        match="unsupported MinerU list child type: 'future_list_child'",
    ):
        map_mineru_middle(payload, descriptor=_descriptor(), run=_run())


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


def test_caption_binding_uses_unique_identity_not_caption_count() -> None:
    result = _result("contract-composites")
    single_caption, same_identity, multiple_identities, combined_identities = result.pages[0].tables

    assert len(single_caption.caption_source_object_ids) == 1
    assert len(same_identity.caption_source_object_ids) == 2
    assert multiple_identities.caption_source_object_ids == ()
    assert combined_identities.caption_source_object_ids == ()

    same_identity_captions = [
        element
        for element in result.pages[0].elements
        if element.source_object_id in same_identity.caption_source_object_ids
    ]
    assert len(same_identity_captions) == 2
    assert all(
        caption.caption_for_source_object_id == same_identity.source_object_id
        and caption.parent_source_object_id == same_identity.source_object_id
        for caption in same_identity_captions
    )

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-caption-identities"),
    )
    caption_relations = [
        relationship
        for relationship in document.relationships
        if relationship.type is RelationshipType.CAPTION_OF
    ]
    table_targets = {table.table_id: len(table.caption_block_ids) for table in document.tables}
    assert table_targets[document.tables[0].table_id] == 1
    assert table_targets[document.tables[1].table_id] == 2
    assert table_targets[document.tables[2].table_id] == 0
    assert table_targets[document.tables[3].table_id] == 0
    assert sum(relation.target_id in table_targets for relation in caption_relations) == 3
    assert document.tables[0].logical_row_count == 2
    assert any(cell.column_span == 2 for cell in document.tables[0].cells)


def test_ambiguous_caption_observation_has_no_strong_parent_or_caption_relation() -> None:
    result = _result("contract-composites")
    ambiguous_table_ids = {
        result.pages[0].tables[2].source_object_id,
        result.pages[0].tables[3].source_object_id,
    }
    ambiguous_captions = [
        element
        for element in result.pages[0].elements
        if element.element_type is ExtractedElementType.FIGURE_CAPTION
        and element.metadata.get("org.mineru.composite_parent_source_object_id")
        in ambiguous_table_ids
    ]

    assert len(ambiguous_captions) == 3
    assert all(caption.caption_for_source_object_id is None for caption in ambiguous_captions)
    assert all(caption.parent_source_object_id is None for caption in ambiguous_captions)
    assert all(
        caption.metadata["org.docparser.structural_ambiguity"] == "MULTIPLE_TABLE_IDENTITIES"
        for caption in ambiguous_captions
    )

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-ambiguous-downgrade"),
    )
    ambiguous_block_ids = {
        block.block_id
        for block in document.pages[0].blocks
        if block.text in {caption.text for caption in ambiguous_captions}
    }
    assert all(
        block.parent_block_id is None
        for block in document.pages[0].blocks
        if block.block_id in ambiguous_block_ids
    )
    assert not any(
        relation.source_id in ambiguous_block_ids and relation.type is RelationshipType.CAPTION_OF
        for relation in document.relationships
    )


def test_discarded_container_preserves_semantic_footnote_and_aside_as_unresolved() -> None:
    result = _result("contract-composites")
    semantic = [
        element
        for element in result.pages[0].elements
        if element.metadata.get("org.mineru.source_layer") == "discarded_blocks"
        and not element.decorative
    ]

    assert [element.element_type for element in semantic] == [
        ExtractedElementType.FOOTNOTE,
        ExtractedElementType.PARAGRAPH,
    ]
    assert all(element.reading_order is None for element in semantic)
    assert all(not element.reading_order_resolved for element in semantic)

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-discarded-semantics"),
    )
    blocks = {block.text: block for block in document.pages[0].blocks}
    assert blocks["Semantic discarded footnote"].block_type is BlockType.FOOTNOTE
    assert blocks["Semantic discarded aside"].block_type is BlockType.PARAGRAPH
    assert (
        blocks["Semantic discarded footnote"].reading_order_status is ReadingOrderStatus.UNRESOLVED
    )
    assert blocks["Semantic discarded aside"].reading_order_status is ReadingOrderStatus.UNRESOLVED
    chunks = fixed_token_chunks(document, CharacterTokenizer(), FixedChunkConfig())
    retrieval_text = "\n".join(chunk.text for chunk in chunks)
    assert "Semantic discarded footnote" in retrieval_text
    assert "Semantic discarded aside" in retrieval_text


def test_code_caption_is_preserved_as_source_text_with_explicit_parent() -> None:
    result = _result("contract-composites")
    code = next(
        element
        for element in result.pages[0].elements
        if element.element_type is ExtractedElementType.CODE
    )
    caption = next(
        element
        for element in result.pages[0].elements
        if element.text == "Synthetic procedure caption"
    )

    assert caption.text is not None
    assert caption.element_type is ExtractedElementType.PARAGRAPH
    assert caption.parent_source_object_id == code.source_object_id
    assert caption.text_spans
    assert (caption.bbox.x0, caption.bbox.y0, caption.bbox.x1, caption.bbox.y1) == (
        20.0,
        320.0,
        580.0,
        338.0,
    )

    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-code-caption"),
    )
    code_block = next(
        block for block in document.pages[0].blocks if block.block_type is BlockType.CODE
    )
    caption_block = next(block for block in document.pages[0].blocks if block.text == caption.text)
    assert caption_block.parent_block_id == code_block.block_id
    assert caption_block.page_number == code_block.page_number == 1
    assert caption_block.text_spans[0].bbox is not None
    chunks = fixed_token_chunks(document, CharacterTokenizer(), FixedChunkConfig())
    assert any(caption.text in chunk.text for chunk in chunks)


def test_unique_generated_visual_analysis_token_never_enters_fixed_chunks() -> None:
    result = _result("contract-composites")
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-visual-isolation"),
    )
    chunks = fixed_token_chunks(document, CharacterTokenizer(), FixedChunkConfig())
    figure = next(
        block for block in document.pages[0].blocks if block.block_type is BlockType.FIGURE
    )
    metadata = figure.extensions["org.docparser.parser_metadata"]

    assert isinstance(metadata, dict)
    assert (
        metadata["org.mineru.generated_visual_analysis"]
        == "SYNTHETIC_VISUAL_INTERPRETATION_TOKEN_9F2A"
    )
    assert all("SYNTHETIC_VISUAL_INTERPRETATION_TOKEN_9F2A" not in chunk.text for chunk in chunks)
    assert any("Synthetic source-grounded figure caption" in chunk.text for chunk in chunks)


def test_multiple_identity_free_captions_use_explicit_composite_hierarchy() -> None:
    payload = _payload("contract-composites")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    tables = cast(list[dict[str, Any]], pages[0]["preproc_blocks"])
    children = cast(list[dict[str, Any]], tables[1]["blocks"])
    first_line = cast(list[dict[str, Any]], children[0]["lines"])[0]
    second_line = cast(list[dict[str, Any]], children[1]["lines"])[0]
    cast(list[dict[str, Any]], first_line["spans"])[0]["content"] = "Synthetic note alpha"
    cast(list[dict[str, Any]], second_line["spans"])[0]["content"] = "Synthetic note beta"

    result = map_mineru_middle(payload, descriptor=_descriptor(), run=_run())

    assert len(result.pages[0].tables[1].caption_source_object_ids) == 2
    assert "org.docparser.structural_ambiguity" not in result.pages[0].tables[1].metadata
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-identity-free-captions"),
    )
    assert len(document.tables[1].caption_block_ids) == 2
    assert (
        sum(
            relation.type is RelationshipType.CAPTION_OF
            and relation.target_id == document.tables[1].table_id
            for relation in document.relationships
        )
        == 2
    )


@pytest.mark.parametrize("block_index", [0, 4, 5])
def test_supported_composites_reject_unknown_child_schema(block_index: int) -> None:
    payload = _payload("contract-composites")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    blocks = cast(list[dict[str, Any]], pages[0]["preproc_blocks"])
    children = cast(list[dict[str, Any]], blocks[block_index]["blocks"])
    children[0]["type"] = "future_composite_annotation"

    with pytest.raises(ValueError, match="unsupported MinerU .* child types"):
        map_mineru_middle(payload, descriptor=_descriptor(), run=_run())


def test_chart_composite_preserves_source_caption_without_generated_analysis_text() -> None:
    payload = _payload("contract-composites")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    blocks = cast(list[dict[str, Any]], pages[0]["preproc_blocks"])
    visual = blocks[5]
    visual["type"] = "chart"
    children = cast(list[dict[str, Any]], visual["blocks"])
    children[0]["type"] = "chart_body"
    children[1]["type"] = "chart_caption"
    body_lines = cast(list[dict[str, Any]], children[0]["lines"])
    body_spans = cast(list[dict[str, Any]], body_lines[0]["spans"])
    body_spans[0]["type"] = "chart"

    result = map_mineru_middle(payload, descriptor=_descriptor(), run=_run())
    visual_elements = [
        element
        for element in result.pages[0].elements
        if element.source_object_id.startswith("mineru:1:preproc:5")
    ]

    assert visual_elements[0].element_type is ExtractedElementType.FIGURE
    assert visual_elements[0].text is None
    assert visual_elements[1].element_type is ExtractedElementType.FIGURE_CAPTION
    assert visual_elements[1].caption_for_source_object_id == visual_elements[0].source_object_id


def test_discarded_blocks_reject_unknown_mineru_schema() -> None:
    payload = _payload("contract-composites")
    pages = cast(list[dict[str, Any]], payload["pdf_info"])
    discarded = cast(list[dict[str, Any]], pages[0]["discarded_blocks"])
    discarded[0]["type"] = "future_margin_object"

    with pytest.raises(ValueError, match="unsupported MinerU discarded block type"):
        map_mineru_middle(payload, descriptor=_descriptor(), run=_run())


def test_parse_result_to_canonical_preserves_all_source_text_geometry_and_spans() -> None:
    result = _result("contract-composites")
    document = normalize_neutral_result(
        result,
        normalization_context(profile_for_result(result), "mineru-generic-fidelity"),
    )
    provenance = {record.provenance_id: record for record in document.provenance}
    canonical_by_source = {
        provenance[block.provenance_ids[0]].original_object_id: block
        for block in document.pages[0].blocks
    }

    for element in result.pages[0].elements:
        block = canonical_by_source[element.source_object_id]
        assert block.text == element.text
        assert block.page_number == element.page_number
        assert block.bbox.root == (
            element.bbox.x0,
            element.bbox.y0,
            element.bbox.x1,
            element.bbox.y1,
        )
        assert [(span.start, span.end) for span in block.text_spans] == [
            (span.start, span.end) for span in element.text_spans
        ]


def test_cross_page_semantic_alignment_builds_one_table_with_precise_segments() -> None:
    result = _result("table-cross-page")
    first, second = result.pages[0].tables[0], result.pages[1].tables[0]
    assert first.continuation_to_source_object_id == second.source_object_id
    assert second.continuation_from_source_object_id == first.source_object_id
    assert second.metadata["org.mineru.repeated_leading_row_count"] == 2

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
    assert any(cell.row_span == 2 for cell in table.cells)
    parser_metadata = table.extensions["org.docparser.parser_metadata"]
    assert isinstance(parser_metadata, dict)
    fragment_metadata = cast(
        list[dict[str, Any]],
        parser_metadata["org.docparser.table_fragment_metadata"],
    )
    assert fragment_metadata[1]["metadata"]["org.mineru.repeated_leading_row_count"] == 2


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
    captions = [
        element
        for element in result.pages[0].elements
        if element.element_type is ExtractedElementType.FIGURE_CAPTION
    ]
    assert len(captions) == 4
    assert all(caption.caption_for_source_object_id is None for caption in captions)
    assert all(caption.parent_source_object_id is None for caption in captions)

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

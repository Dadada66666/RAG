"""Representative complete IR graph fixture."""

from __future__ import annotations

import hashlib

from docparser.ir.chunks import Chunk, ChunkBBox
from docparser.ir.content import Equation, Figure, ReferenceEntry, Section
from docparser.ir.enums import (
    BlockType,
    ChunkType,
    EquationFormat,
    ExtractionMethod,
    RelationshipType,
)
from docparser.ir.geometry import AffineTransform, BBox, Rotation
from docparser.ir.ids import (
    ArtifactId,
    ChunkId,
    EquationId,
    FigureId,
    ProvenanceId,
    ReferenceId,
    RelationshipId,
    SectionId,
    TableCellId,
    TableId,
    TableSegmentId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.models import Block, DocumentIR, Page, ProvenanceRecord
from docparser.ir.relationships import Relationship
from docparser.ir.tables import Table, TableCell, TableCellFragment, TableSegment
from docparser.ir.types import Sha256Digest
from tests.ir_factory import (
    ARTIFACT_ID,
    CONFIG_DIGEST,
    DOCUMENT_ID,
    REVISION_ID,
    TEST_NAMESPACE,
    make_block,
    make_document,
)

PAGE_BBOX = BBox((0.0, 0.0, 595.276, 841.89))
FIGURE_ASSET_ID = ArtifactId("art_018bcfe5-6800-7000-8000-000000000004")


def _id(id_type: type[ProvenanceId], name: str) -> ProvenanceId:
    return generate_uuid5_id(id_type, TEST_NAMESPACE, name)


def _page_provenance(page_number: int, provenance_id: ProvenanceId) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=provenance_id,
        document_id=DOCUMENT_ID,
        source_artifact_id=ARTIFACT_ID,
        page_number=page_number,
        bbox=PAGE_BBOX,
        source_coordinate_space="PDF_USER_SPACE",
        source_bbox=PAGE_BBOX,
        to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
        parser_run_id=None,
        source_parser=None,
        parser_version=None,
        extraction_method=ExtractionMethod.IMPORTED,
        original_object_id=f"page:{page_number}",
        confidence=None,
        char_range=None,
        parent_provenance_ids=(),
        operation="PAGE_CANONICALIZATION",
    )


def _page(page_number: int, blocks: tuple[Block, ...], provenance_id: ProvenanceId) -> Page:
    return Page(
        page_id=generate_page_id(DOCUMENT_ID, page_number),
        page_number=page_number,
        width=595.276,
        height=841.89,
        rotation_applied=Rotation.DEG_0,
        media_box_original=PAGE_BBOX,
        crop_box_original=PAGE_BBOX,
        blocks=blocks,
        page_metadata={"document_type": "MIXED"},
        provenance_ids=(provenance_id,),
        extensions={},
    )


def _cell(
    name: str,
    row: int,
    column: int,
    text: str,
    page_number: int,
    bbox: BBox,
    provenance_id: ProvenanceId,
    *,
    is_header: bool = False,
) -> TableCell:
    return TableCell(
        cell_id=generate_uuid5_id(TableCellId, TEST_NAMESPACE, name),
        row_index=row,
        column_index=column,
        row_span=1,
        column_span=1,
        text=text,
        is_header=is_header,
        page_number=page_number,
        bbox=bbox,
        source_block_ids=(),
        confidence=0.95,
        provenance_ids=(provenance_id,),
        fragments=(),
        extensions={},
    )


def make_full_document() -> DocumentIR:
    """Return a two-page graph with every Phase 2 entity family."""

    base = make_document()
    page1_provenance_id = _id(ProvenanceId, "full-page-1-provenance")
    page2_provenance_id = _id(ProvenanceId, "full-page-2-provenance")
    table_id = generate_uuid5_id(TableId, TEST_NAMESPACE, "full-table")
    figure_id = generate_uuid5_id(FigureId, TEST_NAMESPACE, "full-figure")
    equation_id = generate_uuid5_id(EquationId, TEST_NAMESPACE, "full-equation")
    section_id = generate_uuid5_id(SectionId, TEST_NAMESPACE, "full-section")
    reference_id = generate_uuid5_id(ReferenceId, TEST_NAMESPACE, "full-reference")
    chunk_id = generate_uuid5_id(ChunkId, TEST_NAMESPACE, "full-chunk")
    segment1_id = generate_uuid5_id(TableSegmentId, TEST_NAMESPACE, "full-segment-1")
    segment2_id = generate_uuid5_id(TableSegmentId, TEST_NAMESPACE, "full-segment-2")

    contains_id = generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "full-contains")
    caption_id = generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "full-caption")
    continues_id = generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "full-continues")
    reading_id = generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "full-reading")
    footnote_id = generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "full-footnote")

    title = make_block(
        ordinal=0,
        id_suffix="full-title",
        text="2025 年度报告 / Annual Report",
        bbox=BBox((50.0, 40.0, 545.0, 80.0)),
        provenance_ids=(page1_provenance_id,),
    )
    figure_block = make_block(
        ordinal=1,
        id_suffix="full-figure-block",
        block_type=BlockType.FIGURE,
        text=None,
        bbox=BBox((60.0, 100.0, 535.0, 280.0)),
        relationship_ids=(caption_id,),
        provenance_ids=(page1_provenance_id,),
        content_ref=figure_id,
    )
    figure_caption = make_block(
        ordinal=2,
        id_suffix="full-figure-caption",
        block_type=BlockType.FIGURE_CAPTION,
        text="图 1：年度收入 / Figure 1: Annual revenue",
        bbox=BBox((80.0, 290.0, 515.0, 320.0)),
        relationship_ids=(caption_id,),
        provenance_ids=(page1_provenance_id,),
    )
    table_block1 = make_block(
        ordinal=3,
        id_suffix="full-table-block-1",
        block_type=BlockType.TABLE,
        text=None,
        bbox=BBox((40.0, 350.0, 555.0, 760.0)),
        provenance_ids=(page1_provenance_id,),
        content_ref=table_id,
    )
    table_block2 = make_block(
        ordinal=0,
        id_suffix="full-table-block-2",
        page_number=2,
        block_type=BlockType.TABLE,
        text=None,
        bbox=BBox((40.0, 40.0, 555.0, 300.0)),
        relationship_ids=(footnote_id,),
        provenance_ids=(page2_provenance_id,),
        content_ref=table_id,
    )
    heading = make_block(
        ordinal=1,
        id_suffix="full-heading",
        page_number=2,
        block_type=BlockType.HEADING,
        text="1. 财务摘要 / Financial Summary",
        bbox=BBox((50.0, 330.0, 500.0, 370.0)),
        relationship_ids=(reading_id,),
        provenance_ids=(page2_provenance_id,),
    )
    paragraph = make_block(
        ordinal=2,
        id_suffix="full-paragraph",
        page_number=2,
        text="本年度收入持续增长。Revenue continued to grow during the year.",
        bbox=BBox((50.0, 390.0, 545.0, 450.0)),
        relationship_ids=(reading_id,),
        provenance_ids=(page2_provenance_id,),
    )
    equation_block = make_block(
        ordinal=3,
        id_suffix="full-equation-block",
        page_number=2,
        block_type=BlockType.EQUATION,
        text="R = P \\times Q",
        bbox=BBox((150.0, 480.0, 445.0, 520.0)),
        provenance_ids=(page2_provenance_id,),
        content_ref=equation_id,
    )
    footnote = make_block(
        ordinal=4,
        id_suffix="full-footnote-block",
        page_number=2,
        block_type=BlockType.FOOTNOTE,
        text="注：金额单位为人民币百万元。",
        bbox=BBox((50.0, 760.0, 545.0, 790.0)),
        relationship_ids=(footnote_id,),
        provenance_ids=(page2_provenance_id,),
    )

    segment1 = TableSegment(
        segment_id=segment1_id,
        page_number=1,
        bbox=table_block1.bbox,
        block_id=table_block1.block_id,
        row_start=0,
        row_end_exclusive=2,
        continued_from_segment_id=None,
        continues_to_segment_id=segment2_id,
        provenance_ids=(page1_provenance_id,),
        extensions={},
    )
    segment2 = TableSegment(
        segment_id=segment2_id,
        page_number=2,
        bbox=table_block2.bbox,
        block_id=table_block2.block_id,
        row_start=2,
        row_end_exclusive=3,
        continued_from_segment_id=segment1_id,
        continues_to_segment_id=None,
        provenance_ids=(page2_provenance_id,),
        extensions={},
    )
    merged_cell = TableCell(
        cell_id=generate_uuid5_id(TableCellId, TEST_NAMESPACE, "merged-revenue"),
        row_index=1,
        column_index=0,
        row_span=2,
        column_span=1,
        text="收入 / Revenue",
        is_header=False,
        page_number=1,
        bbox=BBox((40.0, 410.0, 290.0, 760.0)),
        source_block_ids=(),
        confidence=0.92,
        provenance_ids=(page1_provenance_id,),
        fragments=(
            TableCellFragment(
                segment_id=segment1_id,
                page_number=1,
                bbox=BBox((40.0, 410.0, 290.0, 760.0)),
                provenance_ids=(page1_provenance_id,),
            ),
            TableCellFragment(
                segment_id=segment2_id,
                page_number=2,
                bbox=BBox((40.0, 40.0, 290.0, 150.0)),
                provenance_ids=(page2_provenance_id,),
            ),
        ),
        extensions={},
    )
    table = Table(
        table_id=table_id,
        logical_row_count=3,
        logical_column_count=2,
        segments=(segment1, segment2),
        cells=(
            _cell(
                "header-metric",
                0,
                0,
                "指标 / Metric",
                1,
                BBox((40.0, 350.0, 290.0, 400.0)),
                page1_provenance_id,
                is_header=True,
            ),
            _cell(
                "header-value",
                0,
                1,
                "2025",
                1,
                BBox((290.0, 350.0, 555.0, 400.0)),
                page1_provenance_id,
                is_header=True,
            ),
            merged_cell,
            _cell(
                "value-row-1",
                1,
                1,
                "120",
                1,
                BBox((290.0, 410.0, 555.0, 760.0)),
                page1_provenance_id,
            ),
            _cell(
                "value-row-2",
                2,
                1,
                "128",
                2,
                BBox((290.0, 40.0, 555.0, 150.0)),
                page2_provenance_id,
            ),
        ),
        caption_block_ids=(),
        header_row_indices=(0,),
        provenance_ids=(page1_provenance_id, page2_provenance_id),
        confidence=0.93,
        extensions={},
    )
    section = Section(
        section_id=section_id,
        level=1,
        heading_block_id=heading.block_id,
        parent_section_id=None,
        child_section_ids=(),
        content_block_ids=(
            table_block1.block_id,
            table_block2.block_id,
            paragraph.block_id,
            equation_block.block_id,
            footnote.block_id,
        ),
        page_start=1,
        page_end=2,
        provenance_ids=(page2_provenance_id,),
        extensions={},
    )
    figure = Figure(
        figure_id=figure_id,
        block_ids=(figure_block.block_id,),
        caption_block_ids=(figure_caption.block_id,),
        page_numbers=(1,),
        asset_artifact_ids=(FIGURE_ASSET_ID,),
        provenance_ids=(page1_provenance_id,),
        confidence=0.92,
        extensions={},
    )
    equation = Equation(
        equation_id=equation_id,
        block_id=equation_block.block_id,
        text="R = P \\times Q",
        format=EquationFormat.LATEX,
        label=None,
        provenance_ids=(page2_provenance_id,),
        confidence=0.89,
        extensions={},
    )
    reference = ReferenceEntry(
        reference_id=reference_id,
        label="[1]",
        raw_text="Example Group. 2025 Annual Report.",
        field_values={"title": "Annual Report", "year": "2025"},
        source_block_ids=(paragraph.block_id,),
        provenance_ids=(page2_provenance_id,),
        confidence=0.71,
        extensions={},
    )

    chunk_text = f"{heading.text}\n{paragraph.text}"
    chunk_digest = Sha256Digest(f"sha256:{hashlib.sha256(chunk_text.encode()).hexdigest()}")
    chunk = Chunk(
        chunk_id=chunk_id,
        document_id=DOCUMENT_ID,
        ir_revision_id=REVISION_ID,
        chunk_schema_version="1.0.0",
        chunker_version="1.0.0",
        chunk_config_hash=CONFIG_DIGEST,
        chunk_type=ChunkType.PARENT,
        parent_chunk_id=None,
        text=chunk_text,
        parent_section_id=section_id,
        heading_path=(heading.text or "",),
        page_start=2,
        page_end=2,
        source_block_ids=(heading.block_id, paragraph.block_id),
        source_entity_ids=(section_id,),
        bboxes=(ChunkBBox(page_number=2, bbox=BBox((50.0, 330.0, 545.0, 450.0))),),
        content_types=(BlockType.HEADING, BlockType.PARAGRAPH),
        token_count=31,
        tokenizer_id="example-tokenizer@sha256:fixture",
        content_digest=chunk_digest,
        embedding_input_digest=chunk_digest,
        embedding_eligible=True,
        sparse_eligible=True,
        metadata={},
        provenance_ids=(page2_provenance_id,),
    )
    relationships = (
        Relationship(
            relationship_id=contains_id,
            type=RelationshipType.CONTAINS,
            source_id=DOCUMENT_ID,
            target_id=section_id,
            confidence=1.0,
            provenance_ids=(page2_provenance_id,),
            metadata={},
            extensions={},
        ),
        Relationship(
            relationship_id=caption_id,
            type=RelationshipType.CAPTION_OF,
            source_id=figure_caption.block_id,
            target_id=figure_id,
            confidence=0.96,
            provenance_ids=(page1_provenance_id,),
            metadata={},
            extensions={},
        ),
        Relationship(
            relationship_id=continues_id,
            type=RelationshipType.CONTINUES_ON,
            source_id=segment1_id,
            target_id=segment2_id,
            confidence=0.99,
            provenance_ids=(page1_provenance_id, page2_provenance_id),
            metadata={},
            extensions={},
        ),
        Relationship(
            relationship_id=reading_id,
            type=RelationshipType.READING_NEXT,
            source_id=heading.block_id,
            target_id=paragraph.block_id,
            confidence=0.97,
            provenance_ids=(page2_provenance_id,),
            metadata={},
            extensions={},
        ),
        Relationship(
            relationship_id=footnote_id,
            type=RelationshipType.FOOTNOTE_OF,
            source_id=footnote.block_id,
            target_id=table_id,
            confidence=0.82,
            provenance_ids=(page2_provenance_id,),
            metadata={},
            extensions={},
        ),
    )

    return DocumentIR(
        schema_version="1.2.0",
        document_id=DOCUMENT_ID,
        revision_id=REVISION_ID,
        revision_number=0,
        previous_revision_id=None,
        created_at=base.created_at,
        source=base.source,
        metadata=base.metadata,
        processing=base.processing.model_copy(
            update={"artifact_ids": (ARTIFACT_ID, FIGURE_ASSET_ID)}
        ),
        page_count=2,
        pages=(
            _page(1, (title, figure_block, figure_caption, table_block1), page1_provenance_id),
            _page(
                2,
                (table_block2, heading, paragraph, equation_block, footnote),
                page2_provenance_id,
            ),
        ),
        sections=(section,),
        tables=(table,),
        figures=(figure,),
        equations=(equation,),
        references=(reference,),
        chunks=(chunk,),
        relationships=relationships,
        provenance=(
            _page_provenance(1, page1_provenance_id),
            _page_provenance(2, page2_provenance_id),
        ),
        quality_summary=base.quality_summary,
        extensions={},
    )

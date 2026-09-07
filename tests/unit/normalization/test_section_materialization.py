from __future__ import annotations

from dataclasses import dataclass

from tests.ir_factory import ARTIFACT_ID, DOCUMENT_ID, TEST_NAMESPACE, make_document

from docparser.ir.content import IssueCounts, QualitySummary
from docparser.ir.enums import (
    BlockType,
    ConfidenceSource,
    ExtractionMethod,
    QualityStatus,
    ReadingOrderStatus,
    TableCellHeaderRole,
    TextDirection,
)
from docparser.ir.geometry import AffineTransform, BBox, Rotation
from docparser.ir.ids import (
    BlockId,
    ProvenanceId,
    RevisionId,
    TableCellId,
    TableId,
    TableSegmentId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.invariants import validate_document_invariants
from docparser.ir.models import Block, DocumentIR, Page, ProvenanceRecord
from docparser.ir.serialization import dump_canonical_json
from docparser.ir.tables import Table, TableCell, TableSegment
from docparser.ir.types import UtcTimestamp
from docparser.normalization.sections import (
    SECTION_MATERIALIZER_VERSION,
    materialize_sections,
    section_materialization_diagnostics,
)

_PAGE_BBOX = BBox((0.0, 0.0, 600.0, 800.0))
_NEW_REVISION_ID = RevisionId("rev_018bcfe5-6800-7000-8000-000000000010")
_MATERIALIZED_AT = UtcTimestamp("2026-09-07T08:00:00Z")


@dataclass(frozen=True, slots=True)
class _BlockSpec:
    name: str
    block_type: BlockType
    status: ReadingOrderStatus = ReadingOrderStatus.IN_FLOW


def _provenance(
    provenance_id: ProvenanceId,
    *,
    page_number: int,
    bbox: BBox,
    parent_ids: tuple[ProvenanceId, ...],
    object_id: str,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=provenance_id,
        document_id=DOCUMENT_ID,
        source_artifact_id=ARTIFACT_ID,
        page_number=page_number,
        bbox=bbox,
        source_coordinate_space="CANONICAL_PAGE_POINTS",
        source_bbox=bbox,
        to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
        parser_run_id=None,
        source_parser=None,
        parser_version=None,
        extraction_method=ExtractionMethod.IMPORTED,
        original_object_id=object_id,
        confidence=None,
        char_range=None,
        parent_provenance_ids=parent_ids,
        operation="TEST_EVIDENCE",
    )


def _document(*page_specs: tuple[_BlockSpec, ...]) -> DocumentIR:
    pages: list[Page] = []
    provenance: list[ProvenanceRecord] = []
    tables: list[Table] = []
    for page_number, specs in enumerate(page_specs, start=1):
        page_provenance_id = generate_uuid5_id(
            ProvenanceId, TEST_NAMESPACE, "section-test-page", str(page_number)
        )
        provenance.append(
            _provenance(
                page_provenance_id,
                page_number=page_number,
                bbox=_PAGE_BBOX,
                parent_ids=(),
                object_id=f"page:{page_number}",
            )
        )
        blocks: list[Block] = []
        next_order = 0
        for index, spec in enumerate(specs):
            bbox = BBox((40.0, 40.0 + 60.0 * index, 560.0, 80.0 + 60.0 * index))
            block_id = generate_uuid5_id(BlockId, TEST_NAMESPACE, "section-test", spec.name)
            block_provenance_id = generate_uuid5_id(
                ProvenanceId, TEST_NAMESPACE, "section-test", spec.name
            )
            provenance.append(
                _provenance(
                    block_provenance_id,
                    page_number=page_number,
                    bbox=bbox,
                    parent_ids=(page_provenance_id,),
                    object_id=spec.name,
                )
            )
            content_ref = None
            if spec.block_type is BlockType.TABLE:
                table_id = generate_uuid5_id(TableId, TEST_NAMESPACE, "section-test", spec.name)
                segment_id = generate_uuid5_id(
                    TableSegmentId, TEST_NAMESPACE, "section-test", spec.name
                )
                cell_id = generate_uuid5_id(
                    TableCellId, TEST_NAMESPACE, "section-test", spec.name
                )
                content_ref = table_id
                tables.append(
                    Table(
                        table_id=table_id,
                        logical_row_count=1,
                        logical_column_count=1,
                        segments=(
                            TableSegment(
                                segment_id=segment_id,
                                page_number=page_number,
                                bbox=bbox,
                                block_id=block_id,
                                row_start=0,
                                row_end_exclusive=1,
                                continued_from_segment_id=None,
                                continues_to_segment_id=None,
                                provenance_ids=(block_provenance_id,),
                                extensions={},
                            ),
                        ),
                        cells=(
                            TableCell(
                                cell_id=cell_id,
                                row_index=0,
                                column_index=0,
                                row_span=1,
                                column_span=1,
                                text="value",
                                is_header=False,
                                header_role=TableCellHeaderRole.NONE,
                                page_number=page_number,
                                bbox=None,
                                source_block_ids=(),
                                confidence=None,
                                provenance_ids=(block_provenance_id,),
                                fragments=(),
                                extensions={},
                            ),
                        ),
                        caption_block_ids=(),
                        header_row_indices=(),
                        provenance_ids=(block_provenance_id,),
                        confidence=None,
                        extensions={},
                    )
                )
            reading_order = next_order if spec.status is ReadingOrderStatus.IN_FLOW else None
            if reading_order is not None:
                next_order += 1
            blocks.append(
                Block(
                    block_id=block_id,
                    block_type=spec.block_type,
                    page_number=page_number,
                    bbox=bbox,
                    polygon=None,
                    reading_order=reading_order,
                    reading_order_status=spec.status,
                    text=spec.name,
                    text_spans=(),
                    text_direction=TextDirection.LTR,
                    language="en",
                    confidence=None,
                    confidence_source=ConfidenceSource.DERIVED,
                    parent_block_id=None,
                    relationship_ids=(),
                    provenance_ids=(block_provenance_id,),
                    content_ref=content_ref,
                    style=None,
                    extensions={},
                )
            )
        pages.append(
            Page(
                page_id=generate_page_id(DOCUMENT_ID, page_number),
                page_number=page_number,
                width=600.0,
                height=800.0,
                rotation_applied=Rotation.DEG_0,
                media_box_original=_PAGE_BBOX,
                crop_box_original=_PAGE_BBOX,
                blocks=tuple(blocks),
                page_metadata={},
                provenance_ids=(page_provenance_id,),
                extensions={},
            )
        )
    payload = make_document(blocks=()).model_dump(mode="python")
    payload.update(
        {
            "page_count": len(pages),
            "pages": tuple(pages),
            "tables": tuple(tables),
            "provenance": tuple(provenance),
        }
    )
    return DocumentIR.model_validate(payload)


def _materialize(document: DocumentIR) -> DocumentIR:
    return materialize_sections(
        document,
        revision_id_factory=lambda: _NEW_REVISION_ID,
        clock=lambda: _MATERIALIZED_AT,
    )


def test_heading_boundaries_create_flat_sections_and_assign_table_block() -> None:
    document = _document(
        (
            _BlockSpec("Heading A", BlockType.HEADING),
            _BlockSpec("Paragraph A1", BlockType.PARAGRAPH),
            _BlockSpec("Table A", BlockType.TABLE),
            _BlockSpec("Paragraph A2", BlockType.PARAGRAPH),
            _BlockSpec("Heading B", BlockType.HEADING),
            _BlockSpec("Paragraph B1", BlockType.PARAGRAPH),
        )
    )

    result = _materialize(document)

    assert len(result.sections) == 2
    first, second = result.sections
    assert first.heading_block_id == document.pages[0].blocks[0].block_id
    assert first.content_block_ids == tuple(
        block.block_id for block in document.pages[0].blocks[1:4]
    )
    assert document.pages[0].blocks[2].block_id in first.content_block_ids
    assert second.heading_block_id == document.pages[0].blocks[4].block_id
    assert second.content_block_ids == (document.pages[0].blocks[5].block_id,)
    assert all(section.level == 1 for section in result.sections)
    assert all(section.parent_section_id is None for section in result.sections)
    assert all(section.child_section_ids == () for section in result.sections)


def test_text_before_first_heading_becomes_synthetic_preamble() -> None:
    document = _document(
        (
            _BlockSpec("Intro 1", BlockType.PARAGRAPH),
            _BlockSpec("Intro 2", BlockType.PARAGRAPH),
            _BlockSpec("Heading A", BlockType.HEADING),
            _BlockSpec("Body", BlockType.PARAGRAPH),
        )
    )

    result = _materialize(document)

    preamble, headed = result.sections
    assert preamble.heading_block_id is None
    assert preamble.content_block_ids == tuple(
        block.block_id for block in document.pages[0].blocks[:2]
    )
    assert headed.heading_block_id == document.pages[0].blocks[2].block_id
    assert headed.content_block_ids == (document.pages[0].blocks[3].block_id,)


def test_document_without_heading_gets_one_synthetic_body_section() -> None:
    document = _document(
        (
            _BlockSpec("Paragraph 1", BlockType.PARAGRAPH),
            _BlockSpec("Paragraph 2", BlockType.PARAGRAPH),
            _BlockSpec("Table", BlockType.TABLE),
        )
    )

    result = _materialize(document)

    assert len(result.sections) == 1
    assert result.sections[0].heading_block_id is None
    assert result.sections[0].content_block_ids == tuple(
        block.block_id for block in document.pages[0].blocks
    )


def test_decorative_and_unresolved_blocks_remain_in_ir_but_are_not_assigned() -> None:
    document = _document(
        (
            _BlockSpec("Header", BlockType.HEADER, ReadingOrderStatus.DECORATIVE),
            _BlockSpec("Heading", BlockType.HEADING),
            _BlockSpec("Resolved", BlockType.PARAGRAPH),
            _BlockSpec("Unresolved", BlockType.PARAGRAPH, ReadingOrderStatus.UNRESOLVED),
            _BlockSpec("Footer", BlockType.FOOTER, ReadingOrderStatus.DECORATIVE),
            _BlockSpec("Page 1", BlockType.PAGE_NUMBER, ReadingOrderStatus.DECORATIVE),
        )
    )

    result = _materialize(document)
    diagnostics = section_materialization_diagnostics(result)

    assert result.pages == document.pages
    assert result.sections[0].content_block_ids == (document.pages[0].blocks[2].block_id,)
    assert diagnostics.section_count == 1
    assert diagnostics.eligible_block_count == 2
    assert diagnostics.section_assigned_block_count == 2
    assert diagnostics.unassigned_in_flow_block_count == 0
    assert diagnostics.unresolved_retrieval_block_count == 1
    assert diagnostics.section_assignment_coverage == 1.0


def test_sections_span_pages_and_siblings_may_share_a_physical_page() -> None:
    document = _document(
        (
            _BlockSpec("Heading A", BlockType.HEADING),
            _BlockSpec("A page 1", BlockType.PARAGRAPH),
        ),
        (
            _BlockSpec("A page 2", BlockType.PARAGRAPH),
            _BlockSpec("Heading B", BlockType.HEADING),
            _BlockSpec("B page 2", BlockType.PARAGRAPH),
        ),
        (_BlockSpec("B page 3", BlockType.PARAGRAPH),),
    )

    result = _materialize(document)

    assert [(section.page_start, section.page_end) for section in result.sections] == [
        (1, 2),
        (2, 3),
    ]
    validate_document_invariants(result)


def test_materialization_is_deterministic_immutable_and_provenance_resolvable() -> None:
    document = _document(
        (
            _BlockSpec("Heading", BlockType.HEADING),
            _BlockSpec("Body", BlockType.PARAGRAPH),
        )
    )
    before = dump_canonical_json(document)

    first = _materialize(document)
    second = _materialize(document)

    assert first == second
    assert dump_canonical_json(document) == before
    assert first.revision_id == _NEW_REVISION_ID
    assert first.revision_number == document.revision_number + 1
    assert first.previous_revision_id == document.revision_id
    assert first.created_at == _MATERIALIZED_AT
    assert first.pages == document.pages
    assert first.tables == document.tables
    assert first.quality_summary == QualitySummary(
        quality_report_id=None,
        score=None,
        status=QualityStatus.NOT_EVALUATED,
        issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
        publishable=False,
    )
    assert document.quality_summary.status is QualityStatus.PASS
    assert document.quality_summary.quality_report_id is not None
    assert document.quality_summary.publishable is True
    assert first.processing.parser_runs == document.processing.parser_runs
    assert first.processing.pipeline_version == (
        f"{document.processing.pipeline_version}+{SECTION_MATERIALIZER_VERSION}"
    )
    provenance_by_id = {record.provenance_id: record for record in first.provenance}
    for section in first.sections:
        record = provenance_by_id[section.provenance_ids[0]]
        assert record.operation == "SECTION_MATERIALIZATION"
        assert record.extraction_method is ExtractionMethod.DETERMINISTIC_INFERENCE
        assert record.parent_provenance_ids
        assert all(parent_id in provenance_by_id for parent_id in record.parent_provenance_ids)
    validate_document_invariants(first)


def test_materializing_unevaluated_input_keeps_new_revision_unevaluated() -> None:
    document = _document((_BlockSpec("Body", BlockType.PARAGRAPH),))
    payload = document.model_dump(mode="python")
    payload["quality_summary"] = QualitySummary(
        quality_report_id=None,
        score=None,
        status=QualityStatus.NOT_EVALUATED,
        issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
        publishable=False,
    )
    unevaluated = DocumentIR.model_validate(payload)

    result = _materialize(unevaluated)

    assert result.quality_summary.status is QualityStatus.NOT_EVALUATED
    assert result.quality_summary.quality_report_id is None
    assert result.quality_summary.score is None
    assert result.quality_summary.publishable is False
    assert result.quality_summary.issue_counts == IssueCounts(
        INFO=0,
        WARNING=0,
        ERROR=0,
        CRITICAL=0,
    )
    assert result.previous_revision_id == unevaluated.revision_id
    assert result.revision_number == unevaluated.revision_number + 1

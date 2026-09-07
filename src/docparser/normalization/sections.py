"""Deterministic flat section materialization from Canonical IR reading order."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from docparser.ir.content import Section
from docparser.ir.enums import (
    RETRIEVAL_FLOW_BLOCK_TYPES,
    BlockType,
    ExtractionMethod,
    ReadingOrderStatus,
)
from docparser.ir.ids import (
    ProvenanceId,
    RevisionId,
    RevisionIdGenerator,
    SectionId,
    generate_uuid5_id,
)
from docparser.ir.models import Block, DocumentIR, ProvenanceRecord
from docparser.ir.types import UtcTimestamp

SECTION_MATERIALIZER_VERSION = "deterministic-flat-sections@1.0.0"


@dataclass(frozen=True, slots=True)
class SectionMaterializationDiagnostics:
    section_count: int
    eligible_block_count: int
    section_assigned_block_count: int
    unassigned_in_flow_block_count: int
    unresolved_retrieval_block_count: int
    section_assignment_coverage: float | None


@dataclass(slots=True)
class _SectionSource:
    kind: str
    heading: Block | None
    content: list[Block]


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


def _document_namespace(document: DocumentIR) -> UUID:
    return UUID(str(document.document_id).removeprefix("doc_"))


def _reading_order(block: Block) -> int:
    assert block.reading_order is not None
    return block.reading_order


def _ordered_eligible_blocks(document: DocumentIR) -> tuple[Block, ...]:
    ordered: list[Block] = []
    for page in document.pages:
        page_blocks = [
            block
            for block in page.blocks
            if block.reading_order_status is ReadingOrderStatus.IN_FLOW
            and block.block_type in RETRIEVAL_FLOW_BLOCK_TYPES
        ]
        ordered.extend(sorted(page_blocks, key=_reading_order))
    return tuple(ordered)


def _section_sources(blocks: tuple[Block, ...]) -> tuple[_SectionSource, ...]:
    has_heading = any(
        block.block_type in {BlockType.TITLE, BlockType.HEADING} for block in blocks
    )
    if not has_heading:
        return (_SectionSource(kind="body", heading=None, content=list(blocks)),) if blocks else ()

    sources: list[_SectionSource] = []
    current: _SectionSource | None = None
    for block in blocks:
        if block.block_type in {BlockType.TITLE, BlockType.HEADING}:
            current = _SectionSource(kind="heading", heading=block, content=[])
            sources.append(current)
        else:
            if current is None:
                current = _SectionSource(kind="preamble", heading=None, content=[])
                sources.append(current)
            current.content.append(block)
    return tuple(sources)


def _unique_provenance_ids(blocks: Iterable[Block]) -> tuple[ProvenanceId, ...]:
    seen: set[ProvenanceId] = set()
    ordered: list[ProvenanceId] = []
    for block in blocks:
        for provenance_id in block.provenance_ids:
            if provenance_id not in seen:
                seen.add(provenance_id)
                ordered.append(provenance_id)
    return tuple(ordered)


def _build_sections(
    document: DocumentIR,
) -> tuple[tuple[Section, ...], tuple[ProvenanceRecord, ...]]:
    namespace = _document_namespace(document)
    sections: list[Section] = []
    provenance: list[ProvenanceRecord] = []
    for source in _section_sources(_ordered_eligible_blocks(document)):
        identity = str(source.heading.block_id) if source.heading is not None else source.kind
        section_id = generate_uuid5_id(
            SectionId,
            namespace,
            str(document.revision_id),
            "section",
            source.kind,
            identity,
        )
        provenance_id = generate_uuid5_id(
            ProvenanceId,
            namespace,
            str(document.revision_id),
            "section-provenance",
            str(section_id),
        )
        source_blocks = (
            ((source.heading,) if source.heading is not None else ()) + tuple(source.content)
        )
        page_numbers = tuple(block.page_number for block in source_blocks)
        parent_provenance_ids = _unique_provenance_ids(source_blocks)
        sections.append(
            Section(
                section_id=section_id,
                level=1,
                heading_block_id=(
                    source.heading.block_id if source.heading is not None else None
                ),
                parent_section_id=None,
                child_section_ids=(),
                content_block_ids=tuple(block.block_id for block in source.content),
                page_start=min(page_numbers),
                page_end=max(page_numbers),
                provenance_ids=(provenance_id,),
                extensions={},
            )
        )
        provenance.append(
            ProvenanceRecord(
                provenance_id=provenance_id,
                document_id=document.document_id,
                source_artifact_id=document.source.source_artifact_id,
                page_number=None,
                bbox=None,
                source_coordinate_space=None,
                source_bbox=None,
                to_canonical_transform=None,
                parser_run_id=None,
                source_parser=None,
                parser_version=None,
                extraction_method=ExtractionMethod.DETERMINISTIC_INFERENCE,
                original_object_id=f"section:{section_id}",
                confidence=None,
                char_range=None,
                parent_provenance_ids=parent_provenance_ids,
                operation="SECTION_MATERIALIZATION",
            )
        )
    return tuple(sections), tuple(provenance)


def section_materialization_diagnostics(
    document: DocumentIR,
) -> SectionMaterializationDiagnostics:
    """Describe section assignment without treating unresolved blocks as ordered evidence."""

    eligible = _ordered_eligible_blocks(document)
    assigned_ids = {
        block_id
        for section in document.sections
        for block_id in (
            *((section.heading_block_id,) if section.heading_block_id is not None else ()),
            *section.content_block_ids,
        )
    }
    assigned = sum(block.block_id in assigned_ids for block in eligible)
    unresolved = sum(
        block.reading_order_status is ReadingOrderStatus.UNRESOLVED
        and block.block_type in RETRIEVAL_FLOW_BLOCK_TYPES
        for page in document.pages
        for block in page.blocks
    )
    return SectionMaterializationDiagnostics(
        section_count=len(document.sections),
        eligible_block_count=len(eligible),
        section_assigned_block_count=assigned,
        unassigned_in_flow_block_count=len(eligible) - assigned,
        unresolved_retrieval_block_count=unresolved,
        section_assignment_coverage=assigned / len(eligible) if eligible else None,
    )


def materialize_sections(
    document: DocumentIR,
    *,
    revision_id_factory: Callable[[], RevisionId] | None = None,
    clock: Callable[[], UtcTimestamp] = _utc_now,
) -> DocumentIR:
    """Return a new immutable revision containing deterministic flat sections."""

    sections, section_provenance = _build_sections(document)
    payload = document.model_dump(mode="python")
    payload.update(
        {
            "revision_id": (revision_id_factory or RevisionIdGenerator().new)(),
            "revision_number": document.revision_number + 1,
            "previous_revision_id": document.revision_id,
            "created_at": clock(),
            "sections": sections,
            "provenance": document.provenance + section_provenance,
            "processing": document.processing.model_copy(
                update={
                    "pipeline_version": (
                        f"{document.processing.pipeline_version}+{SECTION_MATERIALIZER_VERSION}"
                    )
                }
            ),
        }
    )
    return DocumentIR.model_validate(payload)

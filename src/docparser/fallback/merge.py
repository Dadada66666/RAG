"""Copy-on-write atomic PAGE and TABLE replacement for the fallback MVP."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from docparser.fallback.models import CandidatePage
from docparser.ir.content import Equation, Figure, IssueCounts, QualitySummary
from docparser.ir.enums import QualityStatus
from docparser.ir.ids import (
    ProvenanceId,
    RevisionId,
    RevisionIdGenerator,
    TableCellId,
    generate_uuid5_id,
)
from docparser.ir.models import (
    Block,
    DocumentIR,
    Page,
    ParserScope,
    ProcessingManifest,
    ProvenanceRecord,
    TextSpan,
)
from docparser.ir.relationships import Relationship
from docparser.ir.tables import Table, TableCell, TableCellFragment
from docparser.ir.types import UtcTimestamp

MERGE_VERSION = "fallback-atomic@1.0.0"


class UnsupportedDependencyError(ValueError):
    """An atomic replacement cannot preserve an external IR dependency safely."""


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


def _namespace(document: DocumentIR) -> UUID:
    return UUID(str(document.document_id).removeprefix("doc_"))


def _quality_not_evaluated() -> QualitySummary:
    return QualitySummary(
        quality_report_id=None,
        score=None,
        status=QualityStatus.NOT_EVALUATED,
        issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
        publishable=False,
    )


def _remap_provenance(
    baseline: DocumentIR,
    candidate: CandidatePage,
    *,
    attempt_fingerprint: str,
    operation: str,
) -> tuple[tuple[ProvenanceRecord, ...], dict[ProvenanceId, ProvenanceId]]:
    namespace = _namespace(baseline)
    identifier_map = {
        record.provenance_id: generate_uuid5_id(
            ProvenanceId,
            namespace,
            "fallback",
            attempt_fingerprint,
            str(record.provenance_id),
        )
        for record in candidate.document.provenance
    }
    baseline_page = baseline.pages[candidate.original_page_number - 1]
    records: list[ProvenanceRecord] = []
    for record in candidate.document.provenance:
        parents = tuple(identifier_map[parent] for parent in record.parent_provenance_ids)
        if not parents:
            parents = baseline_page.provenance_ids
        records.append(
            record.model_copy(
                update={
                    "provenance_id": identifier_map[record.provenance_id],
                    "document_id": baseline.document_id,
                    "source_artifact_id": baseline.source.source_artifact_id,
                    "page_number": candidate.original_page_number,
                    "parent_provenance_ids": parents,
                    "operation": operation,
                }
            )
        )
    return tuple(records), identifier_map


def _provenance_ids(
    identifiers: tuple[ProvenanceId, ...],
    mapping: dict[ProvenanceId, ProvenanceId],
) -> tuple[ProvenanceId, ...]:
    return tuple(mapping[identifier] for identifier in identifiers)


def _remap_span(
    span: TextSpan,
    mapping: dict[ProvenanceId, ProvenanceId],
) -> TextSpan:
    return span.model_copy(update={"provenance_ids": _provenance_ids(span.provenance_ids, mapping)})


def _remap_block(
    block: Block,
    page_number: int,
    mapping: dict[ProvenanceId, ProvenanceId],
) -> Block:
    return block.model_copy(
        update={
            "page_number": page_number,
            "text_spans": tuple(_remap_span(span, mapping) for span in block.text_spans),
            "provenance_ids": _provenance_ids(block.provenance_ids, mapping),
        }
    )


def _remap_table(
    table: Table,
    page_number: int,
    mapping: dict[ProvenanceId, ProvenanceId],
) -> Table:
    if len(table.segments) != 1:
        raise UnsupportedDependencyError("cross-page candidate table is outside fallback MVP")
    segments = tuple(
        segment.model_copy(
            update={
                "page_number": page_number,
                "provenance_ids": _provenance_ids(segment.provenance_ids, mapping),
            }
        )
        for segment in table.segments
    )
    cells = tuple(
        cell.model_copy(
            update={
                "page_number": page_number,
                "provenance_ids": _provenance_ids(cell.provenance_ids, mapping),
                "fragments": tuple(
                    fragment.model_copy(
                        update={
                            "page_number": page_number,
                            "provenance_ids": _provenance_ids(fragment.provenance_ids, mapping),
                        }
                    )
                    for fragment in cell.fragments
                ),
            }
        )
        for cell in table.cells
    )
    return table.model_copy(
        update={
            "segments": segments,
            "cells": cells,
            "provenance_ids": _provenance_ids(table.provenance_ids, mapping),
        }
    )


def _processing_with_candidate(
    baseline: DocumentIR, candidate: CandidatePage
) -> ProcessingManifest:
    candidate_runs = tuple(
        run.model_copy(
            update={
                "scope": ParserScope(
                    kind="PAGE",
                    page_numbers=(candidate.original_page_number,),
                    bbox=None,
                ),
                "runtime": {
                    **run.runtime,
                    "materialized_single_page": True,
                    "original_page_number": candidate.original_page_number,
                },
            }
        )
        for run in candidate.document.processing.parser_runs
    )
    return baseline.processing.model_copy(
        update={
            "merge_version": MERGE_VERSION,
            "parser_runs": baseline.processing.parser_runs + candidate_runs,
        }
    )


def _new_revision(
    baseline: DocumentIR,
    *,
    pages: tuple[Page, ...],
    tables: tuple[Table, ...],
    figures: tuple[Figure, ...],
    equations: tuple[Equation, ...],
    relationships: tuple[Relationship, ...],
    provenance: tuple[ProvenanceRecord, ...],
    processing: ProcessingManifest,
    revision_id_factory: Callable[[], RevisionId],
    clock: Callable[[], UtcTimestamp],
) -> DocumentIR:
    payload = baseline.model_dump(mode="python")
    payload.update(
        {
            "schema_version": "1.2.0",
            "revision_id": revision_id_factory(),
            "revision_number": baseline.revision_number + 1,
            "previous_revision_id": baseline.revision_id,
            "created_at": clock(),
            "pages": pages,
            "tables": tables,
            "figures": figures,
            "equations": equations,
            "relationships": relationships,
            "provenance": provenance,
            "processing": processing,
            "quality_summary": _quality_not_evaluated(),
        }
    )
    return DocumentIR.model_validate(payload)


def _page_owned_entities(document: DocumentIR, page_number: int) -> set[str]:
    page = document.pages[page_number - 1]
    identifiers = {str(page.page_id), *(str(block.block_id) for block in page.blocks)}
    identifiers.update(
        str(table.table_id)
        for table in document.tables
        if any(segment.page_number == page_number for segment in table.segments)
    )
    identifiers.update(
        str(figure.figure_id) for figure in document.figures if page_number in figure.page_numbers
    )
    identifiers.update(
        str(equation.equation_id)
        for equation in document.equations
        if any(
            block.block_id == equation.block_id and block.page_number == page_number
            for block in page.blocks
        )
    )
    return identifiers


def _assert_page_replacement_safe(document: DocumentIR, page_number: int) -> set[str]:
    page = document.pages[page_number - 1]
    page_blocks = {block.block_id for block in page.blocks}
    if document.chunks:
        raise UnsupportedDependencyError("page replacement is disabled after chunk derivation")
    if any(
        section.heading_block_id in page_blocks
        or any(block_id in page_blocks for block_id in section.content_block_ids)
        for section in document.sections
    ):
        raise UnsupportedDependencyError("page blocks participate in section dependencies")
    if any(
        any(block_id in page_blocks for block_id in reference.source_block_ids)
        for reference in document.references
    ):
        raise UnsupportedDependencyError("page blocks participate in reference dependencies")
    if any(
        len(table.segments) > 1
        for table in document.tables
        for segment in table.segments
        if segment.page_number == page_number
    ):
        raise UnsupportedDependencyError("page contains a cross-page table")
    if any(
        len(figure.page_numbers) > 1
        for figure in document.figures
        if page_number in figure.page_numbers
    ):
        raise UnsupportedDependencyError("page contains a cross-page figure")
    if any(
        block.parent_block_id is not None
        and ((block.block_id in page_blocks) != (block.parent_block_id in page_blocks))
        for candidate_page in document.pages
        for block in candidate_page.blocks
    ):
        raise UnsupportedDependencyError("page has a cross-page parent block dependency")
    owned = _page_owned_entities(document, page_number)
    if any(
        (str(relationship.source_id) in owned) != (str(relationship.target_id) in owned)
        for relationship in document.relationships
    ):
        raise UnsupportedDependencyError("page has an external relationship dependency")
    return owned


def replace_page_atomic(
    baseline: DocumentIR,
    candidate: CandidatePage,
    *,
    attempt_fingerprint: str,
    triggering_rule_ids: tuple[str, ...],
    revision_id_factory: Callable[[], RevisionId] | None = None,
    clock: Callable[[], UtcTimestamp] = _utc_now,
) -> DocumentIR:
    """Replace one complete page and its page-owned structured entities."""

    source_page = baseline.pages[candidate.original_page_number - 1]
    candidate_page = candidate.document.pages[0]
    if (
        abs(source_page.width - candidate_page.width) > 0.25
        or abs(source_page.height - candidate_page.height) > 0.25
        or source_page.rotation_applied != candidate_page.rotation_applied
    ):
        raise UnsupportedDependencyError("candidate page geometry differs from original page")
    if candidate.document.sections or candidate.document.relationships or candidate.document.chunks:
        raise UnsupportedDependencyError("candidate page has unsupported graph dependencies")
    owned = _assert_page_replacement_safe(baseline, candidate.original_page_number)
    provenance, mapping = _remap_provenance(
        baseline,
        candidate,
        attempt_fingerprint=attempt_fingerprint,
        operation="FALLBACK_PAGE_REPARSE:" + ",".join(triggering_rule_ids),
    )
    remapped_page = candidate_page.model_copy(
        update={
            "page_id": source_page.page_id,
            "page_number": candidate.original_page_number,
            "media_box_original": source_page.media_box_original,
            "crop_box_original": source_page.crop_box_original,
            "blocks": tuple(
                _remap_block(block, candidate.original_page_number, mapping)
                for block in candidate_page.blocks
            ),
            "provenance_ids": _provenance_ids(candidate_page.provenance_ids, mapping),
        }
    )
    pages = tuple(
        remapped_page if page.page_number == candidate.original_page_number else page
        for page in baseline.pages
    )
    tables = tuple(
        table
        for table in baseline.tables
        if not any(
            segment.page_number == candidate.original_page_number for segment in table.segments
        )
    ) + tuple(
        _remap_table(table, candidate.original_page_number, mapping)
        for table in candidate.document.tables
    )
    figures = tuple(
        figure
        for figure in baseline.figures
        if candidate.original_page_number not in figure.page_numbers
    ) + tuple(
        figure.model_copy(
            update={
                "page_numbers": (candidate.original_page_number,),
                "provenance_ids": _provenance_ids(figure.provenance_ids, mapping),
            }
        )
        for figure in candidate.document.figures
    )
    equations = tuple(
        equation for equation in baseline.equations if str(equation.equation_id) not in owned
    ) + tuple(
        equation.model_copy(
            update={"provenance_ids": _provenance_ids(equation.provenance_ids, mapping)}
        )
        for equation in candidate.document.equations
    )
    relationships = tuple(
        relationship
        for relationship in baseline.relationships
        if str(relationship.source_id) not in owned and str(relationship.target_id) not in owned
    )
    return _new_revision(
        baseline,
        pages=pages,
        tables=tables,
        figures=figures,
        equations=equations,
        relationships=relationships,
        provenance=baseline.provenance + provenance,
        processing=_processing_with_candidate(baseline, candidate),
        revision_id_factory=revision_id_factory or RevisionIdGenerator().new,
        clock=clock,
    )


def replace_table_atomic(
    baseline: DocumentIR,
    candidate_page: CandidatePage,
    baseline_table: Table,
    candidate_table: Table,
    *,
    attempt_fingerprint: str,
    triggering_rule_ids: tuple[str, ...],
    revision_id_factory: Callable[[], RevisionId] | None = None,
    clock: Callable[[], UtcTimestamp] = _utc_now,
) -> DocumentIR:
    """Replace one single-page logical table and its table block as a unit."""

    if len(baseline_table.segments) != 1 or len(candidate_table.segments) != 1:
        raise UnsupportedDependencyError("cross-page table fallback is unsupported")
    baseline_segment = baseline_table.segments[0]
    candidate_segment = candidate_table.segments[0]
    page_number = baseline_segment.page_number
    provenance, mapping = _remap_provenance(
        baseline,
        candidate_page,
        attempt_fingerprint=attempt_fingerprint,
        operation="FALLBACK_TABLE_REPLACE:" + ",".join(triggering_rule_ids),
    )
    candidate_block = next(
        block
        for block in candidate_page.document.pages[0].blocks
        if block.block_id == candidate_segment.block_id
    )
    baseline_page = baseline.pages[page_number - 1]
    baseline_block = next(
        block for block in baseline_page.blocks if block.block_id == baseline_segment.block_id
    )
    replacement_block = candidate_block.model_copy(
        update={
            "block_id": baseline_block.block_id,
            "page_number": page_number,
            "reading_order": baseline_block.reading_order,
            "reading_order_status": baseline_block.reading_order_status,
            "parent_block_id": baseline_block.parent_block_id,
            "relationship_ids": baseline_block.relationship_ids,
            "content_ref": baseline_table.table_id,
            "provenance_ids": _provenance_ids(candidate_block.provenance_ids, mapping),
            "semantic_fingerprint": None,
        }
    )
    replacement_segment = candidate_segment.model_copy(
        update={
            "segment_id": baseline_segment.segment_id,
            "page_number": page_number,
            "block_id": baseline_block.block_id,
            "provenance_ids": _provenance_ids(candidate_segment.provenance_ids, mapping),
        }
    )
    replacement_cells: list[TableCell] = []
    for cell in candidate_table.cells:
        fragments = tuple(
            TableCellFragment(
                segment_id=baseline_segment.segment_id,
                page_number=page_number,
                bbox=fragment.bbox,
                provenance_ids=_provenance_ids(fragment.provenance_ids, mapping),
            )
            for fragment in cell.fragments
        )
        replacement_cells.append(
            cell.model_copy(
                update={
                    "cell_id": generate_uuid5_id(
                        TableCellId,
                        _namespace(baseline),
                        "fallback-cell",
                        attempt_fingerprint,
                        str(cell.cell_id),
                    ),
                    "page_number": page_number,
                    "source_block_ids": (),
                    "provenance_ids": _provenance_ids(cell.provenance_ids, mapping),
                    "fragments": fragments,
                }
            )
        )
    replacement_table = candidate_table.model_copy(
        update={
            "table_id": baseline_table.table_id,
            "segments": (replacement_segment,),
            "cells": tuple(replacement_cells),
            "caption_block_ids": baseline_table.caption_block_ids,
            "provenance_ids": _provenance_ids(candidate_table.provenance_ids, mapping),
        }
    )
    pages = tuple(
        page.model_copy(
            update={
                "blocks": tuple(
                    replacement_block if block.block_id == baseline_block.block_id else block
                    for block in page.blocks
                )
            }
        )
        if page.page_number == page_number
        else page
        for page in baseline.pages
    )
    tables = tuple(
        replacement_table if table.table_id == baseline_table.table_id else table
        for table in baseline.tables
    )
    return _new_revision(
        baseline,
        pages=pages,
        tables=tables,
        figures=baseline.figures,
        equations=baseline.equations,
        relationships=baseline.relationships,
        provenance=baseline.provenance + provenance,
        processing=_processing_with_candidate(baseline, candidate_page),
        revision_id_factory=revision_id_factory or RevisionIdGenerator().new,
        clock=clock,
    )

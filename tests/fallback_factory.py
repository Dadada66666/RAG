"""Deterministic multi-page and table fixtures for atomic fallback tests."""

from __future__ import annotations

from uuid import UUID

from docparser.ir.content import IssueCounts, QualitySummary
from docparser.ir.enums import Determinism, ExtractionMethod, QualityStatus
from docparser.ir.geometry import AffineTransform, BBox
from docparser.ir.ids import (
    BlockId,
    ParserRunId,
    ProvenanceId,
    TableCellId,
    TableId,
    TableSegmentId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.models import (
    DocumentIR,
    Page,
    ParserRunSummary,
    ParserScope,
    ProvenanceRecord,
)
from docparser.ir.types import UtcTimestamp
from tests.ir_factory import ARTIFACT_ID, DOCUMENT_ID, make_block, make_document

NAMESPACE = UUID("a97be787-3503-5412-b12b-4e187de43f01")


def multi_page_document(page_count: int = 10) -> DocumentIR:
    base = make_document()
    pages: list[Page] = []
    provenance: list[ProvenanceRecord] = []
    page_bbox = BBox((0.0, 0.0, 595.276, 841.89))
    for page_number in range(1, page_count + 1):
        page_prov = generate_uuid5_id(ProvenanceId, NAMESPACE, "page", str(page_number))
        block_prov = generate_uuid5_id(ProvenanceId, NAMESPACE, "block", str(page_number))
        block_id = generate_uuid5_id(BlockId, NAMESPACE, "block", str(page_number))
        block = (
            base.pages[0]
            .blocks[0]
            .model_copy(
                update={
                    "block_id": block_id,
                    "page_number": page_number,
                    "text": f"baseline page {page_number}",
                    "provenance_ids": (block_prov,),
                }
            )
        )
        pages.append(
            base.pages[0].model_copy(
                update={
                    "page_id": generate_page_id(DOCUMENT_ID, page_number),
                    "page_number": page_number,
                    "blocks": (block,),
                    "provenance_ids": (page_prov,),
                }
            )
        )
        provenance.extend(
            (
                ProvenanceRecord(
                    provenance_id=page_prov,
                    document_id=DOCUMENT_ID,
                    source_artifact_id=ARTIFACT_ID,
                    page_number=page_number,
                    bbox=page_bbox,
                    source_coordinate_space="PDF_USER_SPACE",
                    source_bbox=page_bbox,
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
                ),
                ProvenanceRecord(
                    provenance_id=block_prov,
                    document_id=DOCUMENT_ID,
                    source_artifact_id=ARTIFACT_ID,
                    page_number=page_number,
                    bbox=block.bbox,
                    source_coordinate_space="CANONICAL_PAGE_POINTS",
                    source_bbox=block.bbox,
                    to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
                    parser_run_id=None,
                    source_parser=None,
                    parser_version=None,
                    extraction_method=ExtractionMethod.PDF_TEXT,
                    original_object_id=f"text:{page_number}",
                    confidence=None,
                    char_range=None,
                    parent_provenance_ids=(page_prov,),
                    operation="NORMALIZE_BLOCK",
                ),
            )
        )
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "page_count": page_count,
            "pages": tuple(pages),
            "provenance": tuple(provenance),
            "quality_summary": QualitySummary(
                quality_report_id=None,
                score=None,
                status=QualityStatus.NOT_EVALUATED,
                issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
                publishable=False,
            ),
        }
    )
    return DocumentIR.model_validate(payload)


def candidate_document(text: str = "candidate repaired page") -> DocumentIR:
    candidate_block = make_block(id_suffix="fallback-candidate", text=text)
    base = make_document(blocks=(candidate_block,))
    parser_run_id = ParserRunId("prun_018bcfe5-6800-7000-8000-000000000077")
    parser_run = ParserRunSummary(
        parser_run_id=parser_run_id,
        adapter_id="org.docparser.adapter.fallback-test",
        adapter_version="1.0.0",
        parser_name="alternate-parser",
        parser_version="1.0.0",
        model_ids=(),
        capabilities_used=("LAYOUT",),
        scope=ParserScope(kind="DOCUMENT", page_numbers=(1,), bbox=None),
        started_at=UtcTimestamp("2026-09-02T08:00:00Z"),
        ended_at=UtcTimestamp("2026-09-02T08:00:01Z"),
        device_class="cpu",
        determinism=Determinism.DETERMINISTIC,
        runtime={"org.docparser.fixture": "fallback-candidate"},
    )
    records = tuple(
        record.model_copy(
            update={
                "parser_run_id": parser_run_id,
                "source_parser": "alternate-parser",
                "parser_version": "1.0.0",
                "extraction_method": ExtractionMethod.VLM,
            }
        )
        for record in base.provenance
    )
    payload = base.model_dump(mode="python")
    payload.update(
        {
            "processing": base.processing.model_copy(update={"parser_runs": (parser_run,)}),
            "provenance": records,
            "quality_summary": QualitySummary(
                quality_report_id=None,
                score=None,
                status=QualityStatus.NOT_EVALUATED,
                issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
                publishable=False,
            ),
        }
    )
    return DocumentIR.model_validate(payload)


def with_alternate_run(document: DocumentIR) -> DocumentIR:
    original_run = document.processing.parser_runs[0]
    parser_run_id = ParserRunId("prun_018bcfe5-6800-7000-8000-000000000078")
    run = original_run.model_copy(
        update={
            "parser_run_id": parser_run_id,
            "adapter_id": "org.docparser.adapter.alternate-test",
            "parser_name": "alternate-parser",
        }
    )
    provenance = tuple(
        record.model_copy(
            update={
                "parser_run_id": parser_run_id if record.parser_run_id is not None else None,
                "source_parser": ("alternate-parser" if record.source_parser is not None else None),
            }
        )
        for record in document.provenance
    )
    payload = document.model_dump(mode="python")
    payload.update(
        {
            "processing": document.processing.model_copy(update={"parser_runs": (run,)}),
            "provenance": provenance,
        }
    )
    return DocumentIR.model_validate(payload)


def duplicate_table_on_page(document: DocumentIR) -> DocumentIR:
    table = document.tables[0]
    block = next(
        block for block in document.pages[0].blocks if block.block_id == table.segments[0].block_id
    )
    table_id = generate_uuid5_id(TableId, NAMESPACE, "second-table")
    block_id = generate_uuid5_id(BlockId, NAMESPACE, "second-table-block")
    segment_id = generate_uuid5_id(TableSegmentId, NAMESPACE, "second-table-segment")
    duplicate_block = block.model_copy(
        update={
            "block_id": block_id,
            "reading_order": sum(
                candidate.reading_order is not None for candidate in document.pages[0].blocks
            ),
            "content_ref": table_id,
        }
    )
    duplicate_segment = table.segments[0].model_copy(
        update={"segment_id": segment_id, "block_id": block_id}
    )
    duplicate_cells = tuple(
        cell.model_copy(
            update={
                "cell_id": generate_uuid5_id(
                    TableCellId, NAMESPACE, "second-table-cell", str(index)
                ),
                "fragments": tuple(
                    fragment.model_copy(update={"segment_id": segment_id})
                    for fragment in cell.fragments
                ),
            }
        )
        for index, cell in enumerate(table.cells)
    )
    duplicate_table = table.model_copy(
        update={
            "table_id": table_id,
            "segments": (duplicate_segment,),
            "cells": duplicate_cells,
        }
    )
    page = document.pages[0].model_copy(
        update={"blocks": document.pages[0].blocks + (duplicate_block,)}
    )
    payload = document.model_dump(mode="python")
    payload.update({"pages": (page,), "tables": document.tables + (duplicate_table,)})
    return DocumentIR.model_validate(payload)

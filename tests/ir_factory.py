"""Deterministic Phase 1 IR fixtures for offline tests."""

from __future__ import annotations

from uuid import UUID

from docparser.ir.geometry import AffineTransform, BBox, Rotation
from docparser.ir.ids import (
    ArtifactId,
    BlockId,
    DocumentId,
    ProvenanceId,
    RevisionId,
    generate_document_id,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.models import (
    Block,
    BlockType,
    ConfidenceSource,
    DocumentIR,
    DocumentMetadata,
    ExtractionMethod,
    Page,
    ProcessingManifest,
    ProvenanceRecord,
    ReadingOrderStatus,
    SourceDocument,
    TextDirection,
    TextSpan,
    TextStyle,
)
from docparser.ir.types import Sha256Digest, UtcTimestamp

TEST_NAMESPACE = UUID("12345678-1234-5678-9234-567812345678")
SOURCE_DIGEST = Sha256Digest(f"sha256:{'a' * 64}")
CONFIG_DIGEST = Sha256Digest(f"sha256:{'b' * 64}")
DOCUMENT_ID = generate_document_id(TEST_NAMESPACE, "tenant-acme", SOURCE_DIGEST)
REVISION_ID = RevisionId("rev_018bcfe5-6800-7000-8000-000000000001")
ARTIFACT_ID = ArtifactId("art_018bcfe5-6800-7000-8000-000000000002")


def _deterministic_id(id_type: type[BlockId] | type[ProvenanceId], name: str) -> str:
    return str(generate_uuid5_id(id_type, TEST_NAMESPACE, name))


PAGE_PROVENANCE_ID = ProvenanceId(_deterministic_id(ProvenanceId, "page-provenance"))
BLOCK_PROVENANCE_ID = ProvenanceId(_deterministic_id(ProvenanceId, "block-provenance"))


def make_block(
    *,
    ordinal: int = 0,
    text: str | None = "年度报告 / Annual Report",
    bbox: BBox | None = None,
    text_spans: tuple[TextSpan, ...] = (),
    provenance_ids: tuple[ProvenanceId, ...] = (BLOCK_PROVENANCE_ID,),
) -> Block:
    return Block(
        block_id=BlockId(_deterministic_id(BlockId, f"block-{ordinal}")),
        block_type=BlockType.TITLE if ordinal == 0 else BlockType.PARAGRAPH,
        page_number=1,
        bbox=bbox or BBox((50.0, 60.0 + ordinal * 50.0, 545.0, 100.0 + ordinal * 50.0)),
        polygon=None,
        reading_order=ordinal,
        reading_order_status=ReadingOrderStatus.IN_FLOW,
        text=text,
        text_spans=text_spans,
        text_direction=TextDirection.MIXED,
        language="zh-Hans",
        confidence=0.95,
        confidence_source=ConfidenceSource.CALIBRATED,
        parent_block_id=None,
        relationship_ids=(),
        provenance_ids=provenance_ids,
        content_ref=None,
        style=TextStyle(
            font_family="Noto Sans CJK",
            font_size_pt=18.0,
            bold=True,
            italic=False,
            monospace=False,
        ),
        extensions={"org.docling.layout_label": "TITLE"},
    )


def make_document(
    *,
    blocks: tuple[Block, ...] | None = None,
    created_at: str = "2026-08-28T08:30:00Z",
    document_id: DocumentId = DOCUMENT_ID,
) -> DocumentIR:
    page_bbox = BBox((0.0, 0.0, 595.276, 841.89))
    block_bbox = BBox((50.0, 60.0, 545.0, 100.0))
    selected_blocks = blocks if blocks is not None else (make_block(bbox=block_bbox),)
    provenance = (
        ProvenanceRecord(
            provenance_id=PAGE_PROVENANCE_ID,
            document_id=document_id,
            source_artifact_id=ARTIFACT_ID,
            page_number=1,
            bbox=page_bbox,
            source_coordinate_space="PDF_USER_SPACE",
            source_bbox=page_bbox,
            to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
            parser_run_id=None,
            source_parser=None,
            parser_version=None,
            extraction_method=ExtractionMethod.IMPORTED,
            original_object_id="page:1",
            confidence=None,
            char_range=None,
            parent_provenance_ids=(),
            operation="PAGE_CANONICALIZATION",
        ),
        ProvenanceRecord(
            provenance_id=BLOCK_PROVENANCE_ID,
            document_id=document_id,
            source_artifact_id=ARTIFACT_ID,
            page_number=1,
            bbox=block_bbox,
            source_coordinate_space="CANONICAL_PAGE_POINTS",
            source_bbox=block_bbox,
            to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
            parser_run_id=None,
            source_parser=None,
            parser_version=None,
            extraction_method=ExtractionMethod.PDF_TEXT,
            original_object_id="text:0",
            confidence=0.95,
            char_range=None,
            parent_provenance_ids=(PAGE_PROVENANCE_ID,),
            operation="NORMALIZE_BLOCK",
        ),
    )
    return DocumentIR(
        schema_version="1.0.0",
        document_id=document_id,
        revision_id=REVISION_ID,
        revision_number=0,
        previous_revision_id=None,
        created_at=UtcTimestamp(created_at),
        source=SourceDocument(
            source_artifact_id=ARTIFACT_ID,
            sha256=SOURCE_DIGEST,
            media_type="application/pdf",
            size_bytes=1024,
            original_filename_safe="annual-report.pdf",
            ingested_at=UtcTimestamp("2026-08-28T08:20:00Z"),
            source_uri_redacted=None,
            pdf_version="1.7",
            encryption_status="NOT_ENCRYPTED",
        ),
        metadata=DocumentMetadata(
            title="年度报告 / Annual Report",
            authors=("示例集团", "Example Group"),
            languages=("zh-Hans", "en"),
            created_date="2025-12-31",
            custom={},
        ),
        processing=ProcessingManifest(
            pipeline_version="1.0.0",
            normalizer_version="1.0.0",
            validator_ruleset_version="1.0.0",
            merge_version="1.0.0",
            chunker_version="1.0.0",
            renderer_version="test-renderer@1.0.0",
            config_hash=CONFIG_DIGEST,
            parser_runs=(),
            artifact_ids=(ARTIFACT_ID,),
        ),
        page_count=1,
        pages=(
            Page(
                page_id=generate_page_id(document_id, 1),
                page_number=1,
                width=595.276,
                height=841.89,
                rotation_applied=Rotation.DEG_0,
                media_box_original=page_bbox,
                crop_box_original=page_bbox,
                blocks=selected_blocks,
                page_metadata={
                    "document_type": "BORN_DIGITAL",
                    "text_density": 0.22,
                },
                provenance_ids=(PAGE_PROVENANCE_ID,),
                extensions={},
            ),
        ),
        provenance=provenance,
        extensions={},
    )

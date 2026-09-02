from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError
from tests.parser_fixture import (
    SOURCE_DIGEST,
    TEST_NAMESPACE,
    load_contract_result,
    normalization_context,
    normalize_contract_fixture,
    profile_for_result,
)

from docparser.ir.enums import BlockType, QualityStatus, ReadingOrderStatus
from docparser.ir.geometry import BBox
from docparser.ir.ids import ArtifactId, RevisionId, generate_document_id
from docparser.ir.models import DocumentIR
from docparser.ir.serialization import dump_canonical_json, load_canonical_json
from docparser.ir.types import Sha256Digest, UtcTimestamp
from docparser.normalization import (
    NormalizationContext,
    NormalizationError,
    normalize_docling_result,
    normalize_neutral_result,
)
from docparser.preflight import (
    DocumentProfile,
    DocumentType,
    NativeTextEvidence,
    PageProfile,
    TextExtractionStatus,
)


def test_born_digital_normalizes_to_valid_unpublished_ir() -> None:
    document = normalize_contract_fixture("born-digital")

    assert DocumentIR.model_validate(document.model_dump()) == document
    assert document.quality_summary.status is QualityStatus.NOT_EVALUATED
    assert document.quality_summary.score is None
    assert document.quality_summary.quality_report_id is None
    assert not document.quality_summary.publishable


def test_docling_compatibility_entrypoint_has_neutral_normalizer_parity() -> None:
    result = load_contract_result("simple-table")
    context = normalization_context(profile_for_result(result), "simple-table")

    docling_document = normalize_docling_result(result, context)
    neutral_document = normalize_neutral_result(result, context)

    assert dump_canonical_json(docling_document) == dump_canonical_json(neutral_document)


def test_bilingual_unicode_and_rotation_are_preserved() -> None:
    bilingual = normalize_contract_fixture("bilingual")
    rotated = normalize_contract_fixture("rotated")

    assert "年度报告" in (bilingual.pages[0].blocks[0].text or "")
    assert int(rotated.pages[0].rotation_applied) == 90


def test_two_column_order_uses_upstream_evidence() -> None:
    document = normalize_contract_fixture("two-column")

    assert [block.reading_order for block in document.pages[0].blocks] == [0, 1, 2, 3]
    assert all(
        block.reading_order_status is ReadingOrderStatus.IN_FLOW
        for block in document.pages[0].blocks
    )


def test_explicit_docling_parent_maps_to_canonical_block_parent() -> None:
    page = normalize_contract_fixture("born-digital").pages[0]

    assert page.blocks[1].parent_block_id == page.blocks[0].block_id


def test_simple_and_merged_tables_remain_structured() -> None:
    simple = normalize_contract_fixture("simple-table")
    merged = normalize_contract_fixture("merged-table")

    assert simple.pages[0].blocks[0].block_type is BlockType.TABLE
    assert len(simple.tables[0].cells) == 4
    assert merged.tables[0].cells[0].column_span == 2


def test_every_generated_block_has_resolvable_parser_provenance() -> None:
    document = normalize_contract_fixture("bilingual")
    registry = {record.provenance_id: record for record in document.provenance}

    for page in document.pages:
        for block in page.blocks:
            assert block.provenance_ids
            assert all(identifier in registry for identifier in block.provenance_ids)
            assert registry[block.provenance_ids[0]].parser_run_id is not None


def test_missing_parser_page_cannot_publish_partial_ir() -> None:
    result = load_contract_result("born-digital")
    payload = result.model_dump()
    payload["pages_requested"] = (1, 2)
    partial = result.model_validate(payload)
    with pytest.raises(NormalizationError, match="complete ordered pages"):
        context_profile = DocumentProfile(
            document_type=DocumentType.BORN_DIGITAL,
            page_count=2,
            pages=tuple(
                PageProfile(
                    page_number=number,
                    width=612.0,
                    height=792.0,
                    rotation=0,
                    media_box=BBox((0.0, 0.0, 612.0, 792.0)),
                    crop_box=BBox((0.0, 0.0, 612.0, 792.0)),
                    text_extraction_status=TextExtractionStatus.EXTRACTED,
                    has_text_layer=True,
                    text_char_count=1,
                    estimated_text_coverage=0.1,
                    image_count=0,
                    estimated_image_coverage=0.0,
                    likely_scanned=False,
                    likely_image_only=False,
                    native_text_evidence=NativeTextEvidence(
                        page_number=number,
                        text="x",
                        normalized_numeric_tokens=(),
                        extraction_status=TextExtractionStatus.EXTRACTED,
                    ),
                )
                for number in (1, 2)
            ),
            has_text_layer=True,
            scan_ratio=0.0,
            mixed_document=False,
            encrypted=False,
            readable=True,
            warnings=(),
        )
        normalize_docling_result(
            partial,
            NormalizationContext(
                namespace=TEST_NAMESPACE,
                tenant_scope="test",
                document_id=generate_document_id(TEST_NAMESPACE, "test", SOURCE_DIGEST),
                revision_id=RevisionId("rev_018bcfe5-6800-7000-8000-000000000012"),
                source_artifact_id=ArtifactId("art_018bcfe5-6800-7000-8000-000000000013"),
                source_digest=SOURCE_DIGEST,
                source_size_bytes=1,
                original_filename_safe="missing.pdf",
                ingested_at=UtcTimestamp("2026-09-01T00:00:02Z"),
                created_at=UtcTimestamp("2026-09-01T00:00:02Z"),
                config_digest=Sha256Digest(f"sha256:{'d' * 64}"),
                profile=context_profile,
            ),
        )


def test_table_cell_outside_grid_is_rejected() -> None:
    document = normalize_contract_fixture("simple-table")
    payload = cast(dict[str, Any], json.loads(dump_canonical_json(document)))
    payload["tables"][0]["cells"][0]["column_span"] = 3

    with pytest.raises(ValidationError, match="exceeds logical table dimensions"):
        load_canonical_json(json.dumps(payload))

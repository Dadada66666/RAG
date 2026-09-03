"""Shared synthetic Docling contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from docparser.adapters.parsers.docling.mapping import map_docling_document
from docparser.domain.parser_contract import (
    ParserCapability,
    ParserDescriptor,
    ParseResult,
    ParserRun,
    RuntimeDevice,
)
from docparser.ir.geometry import BBox
from docparser.ir.ids import (
    ArtifactId,
    ParserRunId,
    RevisionId,
    generate_document_id,
)
from docparser.ir.models import DocumentIR
from docparser.ir.types import Sha256Digest, UtcTimestamp
from docparser.normalization import NormalizationContext, normalize_docling_result
from docparser.preflight import (
    DocumentProfile,
    DocumentType,
    NativeTextEvidence,
    PageProfile,
    TextExtractionStatus,
    assess_native_text_reliability,
)

TEST_NAMESPACE = UUID("bc1afef4-67df-5ace-a635-30cf89a29fc3")
SOURCE_DIGEST = Sha256Digest(f"sha256:{'c' * 64}")


def _native_text_evidence(
    page_number: int,
    text: str,
    status: TextExtractionStatus,
) -> NativeTextEvidence:
    reliability, control_count, control_ratio = assess_native_text_reliability(text, status)
    return NativeTextEvidence(
        page_number=page_number,
        text=text,
        normalized_numeric_tokens=(),
        extraction_status=status,
        reliability=reliability,
        control_character_count=control_count,
        control_character_ratio=control_ratio,
    )


def load_contract_result(name: str) -> ParseResult:
    payload = json.loads(Path(f"tests/fixtures/docling/{name}.json").read_text(encoding="utf-8"))
    pages_requested = tuple(sorted(int(value) for value in payload["pages"]))
    return map_docling_document(
        payload,
        descriptor=ParserDescriptor(
            parser_name="docling",
            parser_version="2.123.0",
            adapter_id="org.docparser.adapter.docling",
            adapter_version="0.1.0",
            profile="docling-standard",
            capabilities=(
                ParserCapability.OCR,
                ParserCapability.TABLE,
                ParserCapability.FORMULA,
                ParserCapability.FIGURE,
                ParserCapability.LAYOUT,
                ParserCapability.READING_ORDER,
            ),
            model_identifiers=(
                "docling-layout-default@2.123.0",
                "docling-tableformer@accurate",
            ),
        ),
        run=ParserRun(
            parser_run_id=ParserRunId("prun_018bcfe5-6800-7000-8000-000000000011"),
            started_at=UtcTimestamp("2026-09-01T00:00:00Z"),
            ended_at=UtcTimestamp("2026-09-01T00:00:01Z"),
            requested_device=RuntimeDevice.CPU,
            actual_device=RuntimeDevice.CPU,
            determinism="BEST_EFFORT",
            runtime={"org.docparser.profile": "docling-standard"},
        ),
        pages_requested=pages_requested,
    )


def profile_for_result(result: ParseResult, *, scanned: bool = False) -> DocumentProfile:
    page_profiles = tuple(
        PageProfile(
            page_number=page.page_number,
            width=page.width,
            height=page.height,
            rotation=page.rotation,
            media_box=BBox((0.0, 0.0, page.width, page.height)),
            crop_box=BBox((0.0, 0.0, page.width, page.height)),
            text_extraction_status=(
                TextExtractionStatus.EMPTY if scanned else TextExtractionStatus.EXTRACTED
            ),
            has_text_layer=not scanned,
            text_char_count=0 if scanned else 20,
            estimated_text_coverage=0.0 if scanned else 0.1,
            image_count=1 if scanned else 0,
            estimated_image_coverage=1.0 if scanned else 0.0,
            likely_scanned=scanned,
            likely_image_only=scanned,
            native_text_evidence=_native_text_evidence(
                page.page_number,
                "" if scanned else "fixture 184,392.17",
                TextExtractionStatus.EMPTY if scanned else TextExtractionStatus.EXTRACTED,
            ),
        )
        for page in result.pages
    )
    return DocumentProfile(
        document_type=DocumentType.SCANNED if scanned else DocumentType.BORN_DIGITAL,
        page_count=len(page_profiles),
        pages=page_profiles,
        has_text_layer=not scanned,
        scan_ratio=1.0 if scanned else 0.0,
        mixed_document=False,
        encrypted=False,
        readable=True,
        warnings=(),
    )


def normalize_contract_fixture(name: str, *, scanned: bool = False) -> DocumentIR:
    result = load_contract_result(name)
    profile = profile_for_result(result, scanned=scanned)
    return normalize_docling_result(result, normalization_context(profile, name))


def normalization_context(
    profile: DocumentProfile,
    name: str = "fixture",
) -> NormalizationContext:
    document_id = generate_document_id(TEST_NAMESPACE, "test", SOURCE_DIGEST)
    return NormalizationContext(
        namespace=TEST_NAMESPACE,
        tenant_scope="test",
        document_id=document_id,
        revision_id=RevisionId("rev_018bcfe5-6800-7000-8000-000000000012"),
        source_artifact_id=ArtifactId("art_018bcfe5-6800-7000-8000-000000000013"),
        source_digest=SOURCE_DIGEST,
        source_size_bytes=1024,
        original_filename_safe=f"{name}.pdf",
        ingested_at=UtcTimestamp("2026-09-01T00:00:02Z"),
        created_at=UtcTimestamp("2026-09-01T00:00:02Z"),
        config_digest=Sha256Digest(f"sha256:{'d' * 64}"),
        profile=profile,
    )

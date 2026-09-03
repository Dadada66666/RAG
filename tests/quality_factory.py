"""Small deterministic Quality Gate fixtures."""

from __future__ import annotations

from docparser.ir.geometry import BBox
from docparser.ir.models import DocumentIR
from docparser.ir.types import Sha256Digest
from docparser.preflight import (
    DocumentProfile,
    DocumentType,
    NativeTextEvidence,
    PageProfile,
    TextExtractionStatus,
    assess_native_text_reliability,
    extract_numeric_tokens,
)
from docparser.quality import (
    CalibrationProfile,
    CompletenessThresholds,
    RuleAction,
    TableThresholds,
)
from tests.ir_factory import make_block, make_document


def quality_document(text: str = "Annual Report 184,392.17") -> DocumentIR:
    return make_document(blocks=(make_block(text=text),))


def quality_profile(
    text: str = "Annual Report 184,392.17",
    *,
    image_only: bool = False,
) -> DocumentProfile:
    extracted = not image_only
    evidence_text = text if extracted else ""
    status = TextExtractionStatus.EXTRACTED if extracted else TextExtractionStatus.EMPTY
    reliability, control_count, control_ratio = assess_native_text_reliability(
        evidence_text,
        status,
    )
    return DocumentProfile(
        document_type=DocumentType.SCANNED if image_only else DocumentType.BORN_DIGITAL,
        page_count=1,
        pages=(
            PageProfile(
                page_number=1,
                width=595.276,
                height=841.89,
                rotation=0,
                media_box=BBox((0.0, 0.0, 595.276, 841.89)),
                crop_box=BBox((0.0, 0.0, 595.276, 841.89)),
                text_extraction_status=status,
                has_text_layer=extracted,
                text_char_count=len(evidence_text),
                estimated_text_coverage=0.2 if extracted else 0.0,
                image_count=1 if image_only else 0,
                estimated_image_coverage=1.0 if image_only else 0.0,
                likely_scanned=image_only,
                likely_image_only=image_only,
                native_text_evidence=NativeTextEvidence(
                    page_number=1,
                    text=evidence_text,
                    normalized_numeric_tokens=extract_numeric_tokens(evidence_text),
                    extraction_status=status,
                    reliability=reliability,
                    control_character_count=control_count,
                    control_character_ratio=control_ratio,
                ),
            ),
        ),
        has_text_layer=extracted,
        scan_ratio=1.0 if image_only else 0.0,
        mixed_document=False,
        encrypted=False,
        readable=True,
        warnings=(),
    )


def calibration_profile(*, frozen: bool = True) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="quality-test-v1",
        parser_profile="docling-standard",
        dataset_digest=Sha256Digest(f"sha256:{'c' * 64}"),
        ruleset_version="quality-test@1.0.0",
        supported_slices=("test",),
        completeness=CompletenessThresholds(
            minimum_native_characters=10,
            minimum_parser_to_native_ratio=0.7,
        ),
        table=TableThresholds(
            maximum_empty_cell_ratio=0.5,
            minimum_occupied_grid_ratio=0.8,
        ),
        rule_actions={
            "COMPLETENESS.SOURCE_RICH_PARSE_SPARSE": RuleAction.FALLBACK,
            "NUMERIC.NATIVE_PARSER_DISAGREEMENT": RuleAction.FALLBACK,
            "ORDER.UNRESOLVED": RuleAction.FALLBACK,
            "TABLE.DEGENERATE_STRUCTURE": RuleAction.FALLBACK,
        },
        frozen=frozen,
        created_from_commit="test-commit",
        calibration_report_digest=(
            Sha256Digest(f"sha256:{'d' * 64}") if frozen else None
        ),
        calibration_sample_count=20 if frozen else None,
    )

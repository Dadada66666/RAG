"""Deterministic PDF preflight."""

from docparser.preflight.evidence import (
    MAX_RELIABLE_CONTROL_CHARACTER_RATIO,
    NativeNumericToken,
    NativeTextEvidence,
    NativeTextReliability,
    TextExtractionStatus,
    assess_native_text_reliability,
    extract_numeric_tokens,
)
from docparser.preflight.pdf import (
    DocumentProfile,
    DocumentType,
    PageProfile,
    PreflightError,
    inspect_pdf,
    pdf_user_bbox_to_canonical,
    pdf_user_to_canonical_transform,
)

__all__ = [
    "DocumentProfile",
    "DocumentType",
    "PageProfile",
    "PreflightError",
    "MAX_RELIABLE_CONTROL_CHARACTER_RATIO",
    "NativeNumericToken",
    "NativeTextEvidence",
    "NativeTextReliability",
    "TextExtractionStatus",
    "assess_native_text_reliability",
    "extract_numeric_tokens",
    "inspect_pdf",
    "pdf_user_bbox_to_canonical",
    "pdf_user_to_canonical_transform",
]

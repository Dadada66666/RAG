"""Deterministic PDF preflight."""

from docparser.preflight.evidence import (
    NativeNumericToken,
    NativeTextEvidence,
    TextExtractionStatus,
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
    "NativeNumericToken",
    "NativeTextEvidence",
    "TextExtractionStatus",
    "extract_numeric_tokens",
    "inspect_pdf",
    "pdf_user_bbox_to_canonical",
    "pdf_user_to_canonical_transform",
]

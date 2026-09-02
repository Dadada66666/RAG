"""Neutral parser-result normalization."""

from docparser.normalization.base import NormalizationContext, NormalizationError
from docparser.normalization.docling import normalize_docling_result
from docparser.normalization.neutral import normalize_neutral_result
from docparser.normalization.paddleocr_vl import normalize_paddleocr_vl_result

__all__ = [
    "NormalizationContext",
    "NormalizationError",
    "normalize_docling_result",
    "normalize_neutral_result",
    "normalize_paddleocr_vl_result",
]

"""PaddleOCR-VL neutral-result normalization entrypoint."""

from docparser.domain.parser_contract import ParseResult
from docparser.ir.models import DocumentIR
from docparser.normalization.base import NormalizationContext
from docparser.normalization.docling import normalize_neutral_result


def normalize_paddleocr_vl_result(
    result: ParseResult, context: NormalizationContext
) -> DocumentIR:
    return normalize_neutral_result(result, context)

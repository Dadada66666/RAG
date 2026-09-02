"""Compatibility entrypoint for Docling neutral results."""

from docparser.domain.parser_contract import ParseResult
from docparser.ir.models import DocumentIR
from docparser.normalization.base import NormalizationContext
from docparser.normalization.neutral import normalize_neutral_result


def normalize_docling_result(
    result: ParseResult, context: NormalizationContext
) -> DocumentIR:
    """Normalize Docling's parser-neutral envelope without parser SDK coupling."""

    return normalize_neutral_result(result, context)

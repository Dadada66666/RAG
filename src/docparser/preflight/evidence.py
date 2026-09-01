"""Parser-independent native PDF text evidence and conservative numerics."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import Field

from docparser.ir.base import PageNumber, StrictIRModel
from docparser.ir.types import NfcString, NonEmptyNfcString

_NUMERIC_RE = re.compile(
    r"(?<![\w])(?P<currency>[¥￥$€£])?\s*"
    r"(?P<number>[+-]?(?:\d{1,3}(?:[,\u00a0 ]\d{3})+|\d+)(?:[.,]\d+)?)"
    r"(?![\w])"
)


class TextExtractionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class NativeNumericToken(StrictIRModel):
    raw: NonEmptyNfcString
    normalized: NonEmptyNfcString
    currency: NfcString | None = None


class NativeTextEvidence(StrictIRModel):
    page_number: PageNumber
    text: NfcString
    normalized_numeric_tokens: tuple[NativeNumericToken, ...]
    extraction_status: TextExtractionStatus
    source: str = Field(default="PDF_TEXT", frozen=True)


def _normalize_number(number: str) -> str:
    compact = number.replace("\u00a0", " ").replace(" ", "")
    comma_count = compact.count(",")
    dot_count = compact.count(".")
    if comma_count and dot_count:
        decimal = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return compact.replace(thousands, "").replace(decimal, ".")
    if comma_count:
        tail = compact.rsplit(",", 1)[1]
        if comma_count > 1 or len(tail) == 3:
            return compact.replace(",", "")
        return compact.replace(",", ".")
    if dot_count > 1:
        return compact.replace(".", "")
    return compact


def extract_numeric_tokens(text: str) -> tuple[NativeNumericToken, ...]:
    """Extract exact numeric evidence without fuzzy or domain-specific interpretation."""

    normalized_text = unicodedata.normalize("NFKC", text)
    tokens: list[NativeNumericToken] = []
    for match in _NUMERIC_RE.finditer(normalized_text):
        number = match.group("number")
        currency = match.group("currency")
        raw = f"{currency or ''}{number}"
        normalized_number = _normalize_number(number)
        tokens.append(
            NativeNumericToken(
                raw=raw,
                normalized=f"{currency or ''}{normalized_number}",
                currency=currency,
            )
        )
    return tuple(tokens)


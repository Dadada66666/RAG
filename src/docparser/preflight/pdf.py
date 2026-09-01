"""Fast, deterministic PDF signals collected without model inference or OCR."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import Field, model_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from docparser.ir.base import PageNumber, StrictIRModel
from docparser.ir.types import NfcString


class DocumentType(StrEnum):
    BORN_DIGITAL = "BORN_DIGITAL"
    SCANNED = "SCANNED"
    MIXED = "MIXED"
    EMPTY = "EMPTY"


class PageProfile(StrictIRModel):
    page_number: PageNumber
    width: float = Field(strict=True, gt=0.0)
    height: float = Field(strict=True, gt=0.0)
    rotation: int = Field(strict=True)
    has_text_layer: bool
    text_char_count: int = Field(strict=True, ge=0)
    estimated_text_coverage: float = Field(strict=True, ge=0.0, le=1.0)
    image_count: int = Field(strict=True, ge=0)
    estimated_image_coverage: float = Field(strict=True, ge=0.0, le=1.0)
    likely_scanned: bool

    @model_validator(mode="after")
    def _validate_rotation(self) -> Self:
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("PDF page rotation must be 0, 90, 180, or 270")
        return self


class DocumentProfile(StrictIRModel):
    document_type: DocumentType
    page_count: int = Field(strict=True, ge=1)
    pages: tuple[PageProfile, ...]
    has_text_layer: bool
    scan_ratio: float = Field(strict=True, ge=0.0, le=1.0)
    mixed_document: bool
    encrypted: bool
    readable: bool
    warnings: tuple[NfcString, ...]
    heuristic_version: str = "pdf-preflight@1.0.0"

    @model_validator(mode="after")
    def _validate_cardinality(self) -> Self:
        if len(self.pages) != self.page_count:
            raise ValueError("preflight page count must match page profiles")
        return self


class PreflightError(ValueError):
    """The input cannot be cheaply inspected as a readable PDF."""


def _count_images(page: Any) -> int:
    resources = page.get("/Resources")
    if resources is None:
        return 0
    resources = resources.get_object()
    xobjects = resources.get("/XObject")
    if xobjects is None:
        return 0
    xobjects = xobjects.get_object()
    count = 0
    for candidate in xobjects.values():
        obj = candidate.get_object()
        if obj.get("/Subtype") == "/Image":
            count += 1
    return count


def _coverage_estimates(char_count: int, image_count: int, page_area: float) -> tuple[float, float]:
    # Signals only: text uses a conservative characters-per-area proxy; image placement
    # matrices are intentionally not interpreted during this cheap preflight.
    text_coverage = min(1.0, char_count / max(1.0, page_area / 120.0))
    if image_count == 0:
        image_coverage = 0.0
    elif char_count == 0:
        image_coverage = 1.0
    else:
        image_coverage = min(0.9, image_count * 0.2)
    return round(text_coverage, 4), round(image_coverage, 4)


def inspect_pdf(path: Path) -> DocumentProfile:
    """Inspect PDF structure and cheap routing signals without invoking an ML model."""

    try:
        reader = PdfReader(path, strict=True)
        encrypted = bool(reader.is_encrypted)
        if encrypted and reader.decrypt("") == 0:
            raise PreflightError("encrypted PDF requires a password")
        if not reader.pages:
            raise PreflightError("PDF has no pages")
    except (OSError, PdfReadError, ValueError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError(f"unreadable PDF: {exc}") from exc

    profiles: list[PageProfile] = []
    warnings: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf exposes backend-specific extraction failures.
            text = ""
            warnings.append(f"page {page_number}: text layer inspection failed: {exc}")
        char_count = len(text.strip())
        image_count = _count_images(page)
        text_coverage, image_coverage = _coverage_estimates(
            char_count, image_count, width * height
        )
        profiles.append(
            PageProfile(
                page_number=page_number,
                width=width,
                height=height,
                rotation=rotation,
                has_text_layer=char_count > 0,
                text_char_count=char_count,
                estimated_text_coverage=text_coverage,
                image_count=image_count,
                estimated_image_coverage=image_coverage,
                likely_scanned=char_count == 0 and image_count > 0,
            )
        )

    scan_count = sum(page.likely_scanned for page in profiles)
    text_count = sum(page.has_text_layer for page in profiles)
    mixed = scan_count > 0 and text_count > 0
    if mixed:
        document_type = DocumentType.MIXED
    elif scan_count == len(profiles):
        document_type = DocumentType.SCANNED
    elif text_count > 0:
        document_type = DocumentType.BORN_DIGITAL
    else:
        document_type = DocumentType.EMPTY
    return DocumentProfile(
        document_type=document_type,
        page_count=len(profiles),
        pages=tuple(profiles),
        has_text_layer=text_count > 0,
        scan_ratio=round(scan_count / len(profiles), 4),
        mixed_document=mixed,
        encrypted=encrypted,
        readable=True,
        warnings=tuple(warnings),
    )


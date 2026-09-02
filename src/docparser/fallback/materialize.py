"""Selective page execution by lossless single-page PDF materialization."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from docparser.fallback.models import MaterializedPage
from docparser.ir.geometry import BBox, Rotation
from docparser.ir.ids import DocumentId
from docparser.ir.types import Sha256Digest


def _bbox(box: Any) -> BBox:
    return BBox(
        (
            float(box.left),
            float(box.bottom),
            float(box.right),
            float(box.top),
        )
    )


def materialize_page(
    source_pdf: Path,
    source_document_id: DocumentId,
    page_number: int,
    output_pdf: Path,
) -> MaterializedPage:
    """Write exactly one original page while retaining boxes and rotation."""

    reader = PdfReader(source_pdf, strict=True)
    if page_number < 1 or page_number > len(reader.pages):
        raise ValueError("fallback page_number is outside the source PDF")
    source_page = reader.pages[page_number - 1]
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_page(source_page)
    with output_pdf.open("wb") as target:
        writer.write(target)

    materialized_reader = PdfReader(output_pdf, strict=True)
    page = materialized_reader.pages[0]
    media_box = _bbox(page.mediabox)
    crop_box = _bbox(page.cropbox)
    rotation = Rotation(int(page.get("/Rotate", 0) or 0) % 360)
    width, height = crop_box.width, crop_box.height
    if rotation in {Rotation.DEG_90, Rotation.DEG_270}:
        width, height = height, width
    digest = hashlib.sha256(output_pdf.read_bytes()).hexdigest()
    return MaterializedPage(
        source_document_id=source_document_id,
        source_page_number=page_number,
        temporary_pdf=output_pdf,
        media_box=media_box,
        crop_box=crop_box,
        rotation=rotation,
        width=width,
        height=height,
        digest=Sha256Digest(f"sha256:{digest}"),
    )

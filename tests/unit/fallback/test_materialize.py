from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from tests.ir_factory import DOCUMENT_ID

from docparser.fallback import materialize_page
from docparser.ir.geometry import BBox, Rotation


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_materialized_fallback_input_contains_only_target_page_and_keeps_geometry(
    tmp_path: Path,
    rotation: int,
) -> None:
    source = tmp_path / "ten-pages.pdf"
    writer = PdfWriter()
    for page_number in range(1, 11):
        page = writer.add_blank_page(width=612, height=792)
        if page_number == 6:
            page.cropbox.lower_left = (36, 72)
            page.cropbox.upper_right = (576, 720)
            if rotation:
                page.rotate(rotation)
    with source.open("wb") as stream:
        writer.write(stream)

    materialized = materialize_page(source, DOCUMENT_ID, 6, tmp_path / "page-6.pdf")
    reader = PdfReader(materialized.temporary_pdf, strict=True)

    assert len(reader.pages) == 1
    assert materialized.source_page_number == 6
    assert materialized.media_box == BBox((0.0, 0.0, 612.0, 792.0))
    assert materialized.crop_box == BBox((36.0, 72.0, 576.0, 720.0))
    assert materialized.rotation == Rotation(rotation)
    expected = (648.0, 540.0) if rotation in {90, 270} else (540.0, 648.0)
    assert (materialized.width, materialized.height) == expected

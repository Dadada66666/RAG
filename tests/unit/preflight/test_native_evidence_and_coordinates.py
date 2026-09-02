from pathlib import Path

import pytest
from tests.pdf_factory import write_tiny_pdf

from docparser.ir.geometry import BBox
from docparser.preflight import (
    TextExtractionStatus,
    inspect_pdf,
    pdf_user_bbox_to_canonical,
)


def test_native_text_and_numeric_evidence_are_preserved(tmp_path: Path) -> None:
    profile = inspect_pdf(write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric"))
    evidence = profile.pages[0].native_text_evidence

    assert evidence.extraction_status is TextExtractionStatus.EXTRACTED
    assert "184,392.17" in evidence.text
    assert [token.normalized for token in evidence.normalized_numeric_tokens] == ["184392.17"]


def test_cropbox_and_mediabox_are_preserved_independently(tmp_path: Path) -> None:
    profile = inspect_pdf(write_tiny_pdf(tmp_path / "cropped.pdf", layout="cropped"))
    page = profile.pages[0]

    assert page.media_box.root == (0.0, 0.0, 612.0, 792.0)
    assert page.crop_box.root == (36.0, 72.0, 576.0, 720.0)
    assert (page.width, page.height) == (540.0, 648.0)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (10.0, 160.0, 30.0, 190.0)),
        (90, (10.0, 10.0, 40.0, 30.0)),
        (270, (160.0, 170.0, 190.0, 190.0)),
    ],
)
def test_known_pdf_rectangle_maps_to_rotated_crop_space(
    rotation: int, expected: tuple[float, float, float, float]
) -> None:
    crop = BBox((10.0, 20.0, 210.0, 220.0))
    source = BBox((20.0, 30.0, 40.0, 60.0))

    assert pdf_user_bbox_to_canonical(source, crop, rotation).root == expected


def test_rotated_preflight_uses_effective_crop_dimensions(tmp_path: Path) -> None:
    page_90 = inspect_pdf(write_tiny_pdf(tmp_path / "r90.pdf", layout="rotated")).pages[0]
    page_270 = inspect_pdf(write_tiny_pdf(tmp_path / "r270.pdf", layout="rotated-270")).pages[0]

    assert (page_90.rotation, page_90.width, page_90.height) == (90, 792.0, 612.0)
    assert (page_270.rotation, page_270.width, page_270.height) == (270, 792.0, 612.0)

from pathlib import Path

import pytest
from tests.pdf_factory import write_tiny_pdf

from docparser.preflight import DocumentType, PreflightError, inspect_pdf


def test_born_digital_preflight_is_cpu_only_and_deterministic(tmp_path: Path) -> None:
    path = write_tiny_pdf(tmp_path / "born-digital.pdf")

    first = inspect_pdf(path)
    second = inspect_pdf(path)

    assert first == second
    assert first.document_type is DocumentType.BORN_DIGITAL
    assert first.pages[0].has_text_layer


def test_image_only_page_is_a_scanned_routing_signal(tmp_path: Path) -> None:
    profile = inspect_pdf(write_tiny_pdf(tmp_path / "scanned.pdf", layout="scanned"))

    assert profile.document_type is DocumentType.SCANNED
    assert profile.pages[0].likely_scanned
    assert profile.scan_ratio == 1.0


def test_mixed_and_rotated_profiles(tmp_path: Path) -> None:
    mixed = inspect_pdf(write_tiny_pdf(tmp_path / "mixed.pdf", layout="mixed"))
    rotated = inspect_pdf(write_tiny_pdf(tmp_path / "rotated.pdf", layout="rotated"))

    assert mixed.document_type is DocumentType.MIXED
    assert mixed.page_count == 2
    assert rotated.pages[0].rotation == 90


def test_bilingual_text_layer_fixture_is_readable(tmp_path: Path) -> None:
    profile = inspect_pdf(write_tiny_pdf(tmp_path / "bilingual.pdf", layout="bilingual"))

    assert profile.document_type is DocumentType.BORN_DIGITAL
    assert profile.pages[0].text_char_count >= len("Annual report")


def test_unreadable_pdf_fails_preflight(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")

    with pytest.raises(PreflightError, match="unreadable PDF"):
        inspect_pdf(path)

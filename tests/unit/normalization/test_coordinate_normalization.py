from pathlib import Path

import pytest
from tests.parser_fixture import load_contract_result, normalize_contract_fixture
from tests.pdf_factory import write_tiny_pdf
from tests.unit.application.test_parsing import ContractFixtureParser

from docparser.application.parsing import ParsingConfig, parse_document_with_diagnostics
from docparser.domain.parser_contract import RuntimeDevice


def test_docling_bottom_left_bbox_maps_to_known_top_left_position() -> None:
    block = normalize_contract_fixture("born-digital").pages[0].blocks[0]
    cell = normalize_contract_fixture("simple-table").tables[0].cells[0]

    assert block.bbox.root == (60.0, 50.0, 552.0, 90.0)
    assert cell.bbox is not None
    assert cell.bbox.root == (80.0, 92.0, 306.0, 192.0)


@pytest.mark.parametrize(
    ("layout", "rotation"),
    [("rotated", 90), ("rotated-270", 270)],
)
def test_rotated_parser_bbox_is_inside_effective_page_and_keeps_reference_position(
    tmp_path: Path, layout: str, rotation: int
) -> None:
    result = load_contract_result("rotated")
    result = result.model_copy(
        update={"pages": (result.pages[0].model_copy(update={"rotation": rotation}),)}
    )
    outcome = parse_document_with_diagnostics(
        write_tiny_pdf(tmp_path / f"{layout}.pdf", layout=layout),
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=ContractFixtureParser(result),
    )

    page = outcome.document.pages[0]
    assert page.blocks[0].bbox.root == (50.0, 50.0, 742.0, 90.0)
    assert page.geometry.contains_bbox(page.blocks[0].bbox)


def test_cropped_page_scales_parser_space_into_effective_cropbox(tmp_path: Path) -> None:
    outcome = parse_document_with_diagnostics(
        write_tiny_pdf(tmp_path / "cropped.pdf", layout="cropped"),
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=ContractFixtureParser(load_contract_result("born-digital")),
    )
    page = outcome.document.pages[0]

    assert page.crop_box_original is not None
    assert page.crop_box_original.root == (36.0, 72.0, 576.0, 720.0)
    assert page.blocks[0].bbox.root == pytest.approx((52.9412, 40.9091, 487.0588, 73.6364))

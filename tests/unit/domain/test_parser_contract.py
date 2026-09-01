from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from docparser.domain.parser_contract import (
    CoordinateOrigin,
    ParseRequest,
    ParseScope,
    ParseScopeKind,
    RuntimeDevice,
    SourceBBox,
)


def test_document_parse_request_is_strict_and_cpu_is_valid() -> None:
    request = ParseRequest(
        source_path=Path("document.pdf"),
        scope=ParseScope(),
        device=RuntimeDevice.CPU,
    )

    assert request.device is RuntimeDevice.CPU


def test_page_scope_requires_ordered_unique_pages() -> None:
    with pytest.raises(ValidationError, match="ordered and unique"):
        ParseScope(kind=ParseScopeKind.PAGE, page_numbers=(2, 1))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_source_bbox_rejects_non_finite_coordinates(value: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        SourceBBox(
            x0=value,
            y0=0.0,
            x1=10.0,
            y1=10.0,
            origin=CoordinateOrigin.TOP_LEFT,
        )


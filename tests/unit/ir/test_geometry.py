import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from docparser.ir.geometry import AffineTransform, BBox, PageGeometry, Point, Rotation


@given(
    x0=st.integers(min_value=0, max_value=500),
    y0=st.integers(min_value=0, max_value=700),
    width=st.integers(min_value=1, max_value=94),
    height=st.integers(min_value=1, max_value=140),
)
def test_bbox_invariants(x0: int, y0: int, width: int, height: int) -> None:
    bbox = BBox((x0, y0, x0 + width, y0 + height))

    assert bbox.width == width
    assert bbox.height == height
    assert PageGeometry((595.0, 842.0, Rotation.DEG_0)).contains_bbox(bbox)


@pytest.mark.parametrize(
    "coordinates",
    [
        (0.0, 0.0, 0.0, 1.0),
        (0.0, 0.0, 1.0, 0.0),
        (math.nan, 0.0, 1.0, 1.0),
        (0.0, 0.0, math.inf, 1.0),
        (0.0, -math.inf, 1.0, 1.0),
    ],
)
def test_invalid_bbox_is_rejected(coordinates: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValidationError):
        BBox(coordinates)


@given(
    x=st.floats(min_value=-10_000, max_value=10_000, allow_nan=False, allow_infinity=False),
    y=st.floats(min_value=-10_000, max_value=10_000, allow_nan=False, allow_infinity=False),
    sx=st.floats(min_value=0.1, max_value=10, allow_nan=False, allow_infinity=False),
    sy=st.floats(min_value=0.1, max_value=10, allow_nan=False, allow_infinity=False),
    tx=st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
    ty=st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False),
)
def test_affine_transform_round_trip(
    x: float,
    y: float,
    sx: float,
    sy: float,
    tx: float,
    ty: float,
) -> None:
    point = Point((x, y))
    transform = AffineTransform((sx, 0.0, 0.0, sy, tx, ty))
    page = PageGeometry((595.0, 842.0, Rotation.DEG_0))

    assert transform.round_trip_within_tolerance(point, page)


def test_singular_affine_transform_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invertible"):
        AffineTransform((1.0, 2.0, 2.0, 4.0, 0.0, 0.0))


def test_invalid_rotation_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PageGeometry.model_validate_json("[595.0,842.0,45]")


def test_coordinate_precision_is_bounded() -> None:
    point = Point((1.23456, 2.34565))

    assert point.root == (1.2346, 2.3457)

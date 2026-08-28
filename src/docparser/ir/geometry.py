"""Canonical page-space geometry and affine transformations."""

from __future__ import annotations

import math
from enum import IntEnum
from typing import Annotated, Self

from pydantic import BeforeValidator, ConfigDict, Field, RootModel, model_validator

COORDINATE_DECIMAL_PLACES = 4
_TRANSFORM_DECIMAL_PLACES = 12


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("value must be finite")
    return 0.0 if number == 0.0 else number


def _coordinate(value: object) -> float:
    return round(_finite_number(value), COORDINATE_DECIMAL_PLACES)


def _transform_number(value: object) -> float:
    return round(_finite_number(value), _TRANSFORM_DECIMAL_PLACES)


FiniteNumber = Annotated[float, BeforeValidator(_finite_number)]
Coordinate = Annotated[float, BeforeValidator(_coordinate)]
PositiveDimension = Annotated[Coordinate, Field(gt=0.0)]
TransformNumber = Annotated[float, BeforeValidator(_transform_number)]


class Rotation(IntEnum):
    DEG_0 = 0
    DEG_90 = 90
    DEG_180 = 180
    DEG_270 = 270


class Point(RootModel[tuple[Coordinate, Coordinate]]):
    """Point in canonical top-left page coordinates."""

    model_config = ConfigDict(strict=True, frozen=True)

    @property
    def x(self) -> float:
        return self.root[0]

    @property
    def y(self) -> float:
        return self.root[1]


class BBox(RootModel[tuple[Coordinate, Coordinate, Coordinate, Coordinate]]):
    """Half-open axis-aligned bounding box ``[x0, y0, x1, y1]``."""

    model_config = ConfigDict(strict=True, frozen=True)

    @model_validator(mode="after")
    def _validate_area(self) -> Self:
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("bbox must have positive width and height")
        return self

    @property
    def x0(self) -> float:
        return self.root[0]

    @property
    def y0(self) -> float:
        return self.root[1]

    @property
    def x1(self) -> float:
        return self.root[2]

    @property
    def y1(self) -> float:
        return self.root[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def corners(self) -> tuple[Point, Point, Point, Point]:
        return (
            Point((self.x0, self.y0)),
            Point((self.x1, self.y0)),
            Point((self.x1, self.y1)),
            Point((self.x0, self.y1)),
        )


class AffineTransform(
    RootModel[
        tuple[
            TransformNumber,
            TransformNumber,
            TransformNumber,
            TransformNumber,
            TransformNumber,
            TransformNumber,
        ]
    ]
):
    """Six-parameter affine transform from source to canonical coordinates."""

    model_config = ConfigDict(strict=True, frozen=True)

    @model_validator(mode="after")
    def _validate_invertible(self) -> Self:
        if math.isclose(self.determinant, 0.0, abs_tol=1e-15):
            raise ValueError("affine transform must be invertible")
        return self

    @property
    def determinant(self) -> float:
        a, b, c, d, _, _ = self.root
        return a * d - b * c

    def apply(self, point: Point) -> Point:
        a, b, c, d, e, f = self.root
        return Point((a * point.x + c * point.y + e, b * point.x + d * point.y + f))

    def inverse(self) -> AffineTransform:
        a, b, c, d, e, f = self.root
        determinant = self.determinant
        return AffineTransform(
            (
                d / determinant,
                -b / determinant,
                -c / determinant,
                a / determinant,
                (c * f - d * e) / determinant,
                (b * e - a * f) / determinant,
            )
        )

    def round_trip_error(self, point: Point) -> float:
        restored = self.inverse().apply(self.apply(point))
        return math.hypot(restored.x - point.x, restored.y - point.y)

    def round_trip_within_tolerance(self, point: Point, page: PageGeometry) -> bool:
        return self.round_trip_error(point) <= page.transform_tolerance


class PageGeometry(RootModel[tuple[PositiveDimension, PositiveDimension, Rotation]]):
    """Canonical page dimensions and applied rotation."""

    model_config = ConfigDict(strict=True, frozen=True)

    @property
    def width(self) -> float:
        return self.root[0]

    @property
    def height(self) -> float:
        return self.root[1]

    @property
    def rotation(self) -> Rotation:
        return self.root[2]

    @property
    def transform_tolerance(self) -> float:
        return max(0.25, 0.001 * max(self.width, self.height))

    def contains_point(self, point: Point) -> bool:
        return 0.0 <= point.x <= self.width and 0.0 <= point.y <= self.height

    def contains_bbox(self, bbox: BBox) -> bool:
        return (
            0.0 <= bbox.x0 < bbox.x1 <= self.width
            and 0.0 <= bbox.y0 < bbox.y1 <= self.height
        )


def polygon_is_simple(points: tuple[Point, ...]) -> bool:
    """Return whether a polygon has no self-intersecting non-adjacent edges."""

    if len(points) < 3:
        return False

    def orientation(a: Point, b: Point, c: Point) -> float:
        return (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y)

    def on_segment(a: Point, b: Point, c: Point) -> bool:
        return (
            min(a.x, c.x) <= b.x <= max(a.x, c.x)
            and min(a.y, c.y) <= b.y <= max(a.y, c.y)
        )

    def intersects(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
        o1 = orientation(a1, a2, b1)
        o2 = orientation(a1, a2, b2)
        o3 = orientation(b1, b2, a1)
        o4 = orientation(b1, b2, a2)
        if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
            return True
        return (
            (math.isclose(o1, 0.0) and on_segment(a1, b1, a2))
            or (math.isclose(o2, 0.0) and on_segment(a1, b2, a2))
            or (math.isclose(o3, 0.0) and on_segment(b1, a1, b2))
            or (math.isclose(o4, 0.0) and on_segment(b1, a2, b2))
        )

    edge_count = len(points)
    for first in range(edge_count):
        first_next = (first + 1) % edge_count
        for second in range(first + 1, edge_count):
            second_next = (second + 1) % edge_count
            if first in {second, second_next} or first_next in {second, second_next}:
                continue
            if intersects(points[first], points[first_next], points[second], points[second_next]):
                return False
    return True

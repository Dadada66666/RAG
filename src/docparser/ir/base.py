"""Shared strict model configuration and scalar aliases for Canonical IR."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from docparser.ir.geometry import _finite_number

StrictInt = Annotated[int, Field(strict=True)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PageNumber = Annotated[StrictInt, Field(ge=1)]
Confidence = Annotated[
    float,
    Field(ge=0.0, le=1.0),
    BeforeValidator(_finite_number),
]


class StrictIRModel(BaseModel):
    """Strict, closed base for every object-shaped IR wire model."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
        protected_namespaces=(),
    )

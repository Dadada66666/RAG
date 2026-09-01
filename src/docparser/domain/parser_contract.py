"""Parser-neutral contract shared by adapters and normalization."""

from __future__ import annotations

import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from docparser.ir.base import Confidence, PageNumber, PositiveInt, StrictIRModel
from docparser.ir.ids import ParserRunId
from docparser.ir.types import BoundedJsonObject, NfcString, NonEmptyNfcString, UtcTimestamp


class ParserCapability(StrEnum):
    OCR = "OCR"
    TABLE = "TABLE"
    FORMULA = "FORMULA"
    FIGURE = "FIGURE"
    LAYOUT = "LAYOUT"
    READING_ORDER = "READING_ORDER"


class CoordinateUnit(StrEnum):
    POINT = "POINT"
    PIXEL = "PIXEL"


class ParserHealthStatus(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


class ParseScopeKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    PAGE = "PAGE"


class ParseStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RuntimeDevice(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


class CoordinateOrigin(StrEnum):
    TOP_LEFT = "TOP_LEFT"
    BOTTOM_LEFT = "BOTTOM_LEFT"


class ExtractedElementType(StrEnum):
    TITLE = "TITLE"
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    LIST_ITEM = "LIST_ITEM"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    FIGURE_CAPTION = "FIGURE_CAPTION"
    EQUATION = "EQUATION"
    CODE = "CODE"
    QUOTE = "QUOTE"
    FOOTNOTE = "FOOTNOTE"
    HEADER = "HEADER"
    FOOTER = "FOOTER"
    PAGE_NUMBER = "PAGE_NUMBER"
    UNKNOWN = "UNKNOWN"


class NeutralModel(StrictIRModel):
    """Strict parser contract model with no parser SDK types."""


class SourceBBox(NeutralModel):
    x0: float = Field(strict=True)
    y0: float = Field(strict=True)
    x1: float = Field(strict=True)
    y1: float = Field(strict=True)
    origin: CoordinateOrigin

    @model_validator(mode="after")
    def _validate_area(self) -> Self:
        if not all(math.isfinite(value) for value in (self.x0, self.y0, self.x1, self.y1)):
            raise ValueError("source bbox coordinates must be finite")
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("source bbox must have positive width and height")
        return self


class ParseScope(NeutralModel):
    kind: ParseScopeKind = ParseScopeKind.DOCUMENT
    page_numbers: tuple[PageNumber, ...] = ()

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if tuple(sorted(set(self.page_numbers))) != self.page_numbers:
            raise ValueError("scope page_numbers must be ordered and unique")
        if self.kind is ParseScopeKind.PAGE and not self.page_numbers:
            raise ValueError("PAGE scope requires page_numbers")
        if self.kind is ParseScopeKind.DOCUMENT and self.page_numbers:
            raise ValueError("DOCUMENT scope must not restrict page_numbers")
        return self


class ParseRequest(NeutralModel):
    source_path: Path
    scope: ParseScope = Field(default_factory=ParseScope)
    device: RuntimeDevice = RuntimeDevice.AUTO
    raw_output_dir: Path | None = None


class ParserDescriptor(NeutralModel):
    parser_name: NonEmptyNfcString
    parser_version: NonEmptyNfcString
    adapter_id: NonEmptyNfcString
    adapter_version: NonEmptyNfcString
    profile: NonEmptyNfcString
    capabilities: Annotated[tuple[ParserCapability, ...], Field(min_length=1)]
    supported_scopes: Annotated[tuple[ParseScopeKind, ...], Field(min_length=1)] = (
        ParseScopeKind.DOCUMENT,
    )
    model_identifiers: tuple[NonEmptyNfcString, ...]


class ParserHealth(NeutralModel):
    status: ParserHealthStatus
    requested_device: RuntimeDevice
    actual_device: RuntimeDevice | None
    detail: NfcString | None


class ParserRun(NeutralModel):
    parser_run_id: ParserRunId
    started_at: UtcTimestamp
    ended_at: UtcTimestamp
    requested_device: RuntimeDevice
    actual_device: RuntimeDevice
    determinism: Literal["DETERMINISTIC", "BEST_EFFORT", "NONDETERMINISTIC"]
    runtime: BoundedJsonObject


class ParserError(NeutralModel):
    code: Literal[
        "UNSUPPORTED_DOCUMENT",
        "PARSER_UNAVAILABLE",
        "RUNTIME_UNAVAILABLE",
        "PARSER_FAILURE",
        "INVALID_OUTPUT",
    ]
    message: NonEmptyNfcString
    retryable: bool


class ParserExecutionError(RuntimeError):
    """A complete parser call failed before a usable neutral result existed."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "PARSER_FAILURE",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error = ParserError.model_validate(
            {"code": code, "message": message, "retryable": retryable}
        )


class ExtractedElement(NeutralModel):
    source_object_id: NonEmptyNfcString
    element_type: ExtractedElementType
    page_number: PageNumber
    bbox: SourceBBox
    text: NfcString | None
    reading_order: int | None = Field(default=None, strict=True, ge=0)
    reading_order_resolved: bool
    decorative: bool = False
    language: NfcString | None = None
    confidence: Confidence | None = None
    extraction_method: Literal[
        "PDF_TEXT",
        "OCR",
        "VLM",
        "LAYOUT_MODEL",
        "TABLE_MODEL",
        "FORMULA_MODEL",
        "IMPORTED",
    ]
    parent_source_object_id: NfcString | None = None
    caption_for_source_object_id: NfcString | None = None
    metadata: BoundedJsonObject = Field(default_factory=dict)


class ExtractedTableCell(NeutralModel):
    source_object_id: NonEmptyNfcString
    row_index: int = Field(strict=True, ge=0)
    column_index: int = Field(strict=True, ge=0)
    row_span: PositiveInt
    column_span: PositiveInt
    text: NfcString
    is_header: bool
    bbox: SourceBBox | None
    confidence: Confidence | None = None


class ExtractedTable(NeutralModel):
    source_object_id: NonEmptyNfcString
    page_number: PageNumber
    bbox: SourceBBox
    row_count: PositiveInt
    column_count: PositiveInt
    cells: Annotated[tuple[ExtractedTableCell, ...], Field(min_length=1)]
    caption_source_object_ids: tuple[NfcString, ...] = ()
    continuation_from_source_object_id: NfcString | None = None
    continuation_to_source_object_id: NfcString | None = None
    confidence: Confidence | None = None


class PageParseResult(NeutralModel):
    page_number: PageNumber
    width: float = Field(strict=True, gt=0.0)
    height: float = Field(strict=True, gt=0.0)
    rotation: Literal[0, 90, 180, 270]
    coordinate_unit: CoordinateUnit = CoordinateUnit.POINT
    elements: tuple[ExtractedElement, ...]
    tables: tuple[ExtractedTable, ...]
    warnings: tuple[NfcString, ...] = ()


class ParseResult(NeutralModel):
    status: ParseStatus
    descriptor: ParserDescriptor
    run: ParserRun
    pages_requested: tuple[PageNumber, ...]
    pages: tuple[PageParseResult, ...]
    warnings: tuple[NfcString, ...] = ()
    errors: tuple[ParserError, ...] = ()

    @model_validator(mode="after")
    def _validate_pages(self) -> Self:
        page_numbers = tuple(page.page_number for page in self.pages)
        if tuple(sorted(set(page_numbers))) != page_numbers:
            raise ValueError("parse result pages must be ordered and unique")
        if any(page not in self.pages_requested for page in page_numbers):
            raise ValueError("parsed page was not requested")
        return self

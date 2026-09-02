"""Structured document content entities outside pages and tables."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from docparser.ir.base import (
    Confidence,
    NonNegativeInt,
    PageNumber,
    StrictInt,
    StrictIRModel,
)
from docparser.ir.enums import EquationFormat, QualityStatus
from docparser.ir.ids import (
    ArtifactId,
    BlockId,
    EquationId,
    FigureId,
    ProvenanceId,
    QualityReportId,
    ReferenceId,
    SectionId,
)
from docparser.ir.types import BoundedJsonObject, Extensions, NfcString, NonEmptyNfcString

SectionLevel = Annotated[StrictInt, Field(ge=1, le=12)]


class Section(StrictIRModel):
    section_id: SectionId
    level: SectionLevel
    heading_block_id: BlockId | None
    parent_section_id: SectionId | None
    child_section_ids: tuple[SectionId, ...]
    content_block_ids: tuple[BlockId, ...]
    page_start: PageNumber
    page_end: PageNumber
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_local_structure(self) -> Self:
        if self.page_start > self.page_end:
            raise ValueError("section requires page_start <= page_end")
        if self.section_id in self.child_section_ids:
            raise ValueError("section cannot be its own child")
        if len(set(self.child_section_ids)) != len(self.child_section_ids):
            raise ValueError("child_section_ids must be unique")
        if len(set(self.content_block_ids)) != len(self.content_block_ids):
            raise ValueError("content_block_ids must be unique")
        return self


class Figure(StrictIRModel):
    figure_id: FigureId
    block_ids: Annotated[tuple[BlockId, ...], Field(min_length=1)]
    caption_block_ids: tuple[BlockId, ...]
    page_numbers: Annotated[tuple[PageNumber, ...], Field(min_length=1)]
    asset_artifact_ids: tuple[ArtifactId, ...]
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    confidence: Confidence | None
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_ordered_pages(self) -> Self:
        if tuple(sorted(set(self.page_numbers))) != self.page_numbers:
            raise ValueError("figure page_numbers must be ordered and unique")
        return self


class Equation(StrictIRModel):
    equation_id: EquationId
    block_id: BlockId
    text: NfcString
    format: EquationFormat
    label: NfcString | None
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    confidence: Confidence | None
    extensions: Extensions = Field(default_factory=dict)


class ReferenceEntry(StrictIRModel):
    reference_id: ReferenceId
    label: NfcString
    raw_text: NonEmptyNfcString
    field_values: BoundedJsonObject
    source_block_ids: Annotated[tuple[BlockId, ...], Field(min_length=1)]
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    confidence: Confidence | None
    extensions: Extensions = Field(default_factory=dict)


class IssueCounts(StrictIRModel):
    INFO: NonNegativeInt
    WARNING: NonNegativeInt
    ERROR: NonNegativeInt
    CRITICAL: NonNegativeInt


class QualitySummary(StrictIRModel):
    quality_report_id: QualityReportId | None
    score: Confidence | None
    status: QualityStatus
    issue_counts: IssueCounts
    publishable: bool

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> Self:
        if self.status is QualityStatus.NOT_EVALUATED:
            if self.quality_report_id is not None or self.score is not None:
                raise ValueError("NOT_EVALUATED quality must not declare a report or score")
            if self.publishable:
                raise ValueError("NOT_EVALUATED quality cannot be publishable")
        elif self.quality_report_id is None:
            raise ValueError("evaluated quality requires a report ID")
        return self

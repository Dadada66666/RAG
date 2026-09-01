"""Logical table entities, page segments, cells, and visual fragments."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from docparser.ir.base import (
    Confidence,
    NonNegativeInt,
    PageNumber,
    PositiveInt,
    StrictIRModel,
)
from docparser.ir.geometry import BBox
from docparser.ir.ids import (
    BlockId,
    ProvenanceId,
    TableCellId,
    TableId,
    TableSegmentId,
)
from docparser.ir.types import Extensions, NfcString


class TableCellFragment(StrictIRModel):
    segment_id: TableSegmentId
    page_number: PageNumber
    bbox: BBox
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]


class TableCell(StrictIRModel):
    cell_id: TableCellId
    row_index: NonNegativeInt
    column_index: NonNegativeInt
    row_span: PositiveInt
    column_span: PositiveInt
    text: NfcString
    is_header: bool
    page_number: PageNumber
    bbox: BBox | None
    source_block_ids: tuple[BlockId, ...]
    confidence: Confidence | None
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    fragments: tuple[TableCellFragment, ...] = ()
    extensions: Extensions = Field(default_factory=dict)


class TableSegment(StrictIRModel):
    segment_id: TableSegmentId
    page_number: PageNumber
    bbox: BBox
    block_id: BlockId
    row_start: NonNegativeInt
    row_end_exclusive: PositiveInt
    continued_from_segment_id: TableSegmentId | None
    continues_to_segment_id: TableSegmentId | None
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_row_range(self) -> Self:
        if self.row_start >= self.row_end_exclusive:
            raise ValueError("table segment requires row_start < row_end_exclusive")
        if self.continued_from_segment_id == self.segment_id:
            raise ValueError("table segment cannot continue from itself")
        if self.continues_to_segment_id == self.segment_id:
            raise ValueError("table segment cannot continue to itself")
        return self


class Table(StrictIRModel):
    table_id: TableId
    logical_row_count: PositiveInt
    logical_column_count: PositiveInt
    segments: Annotated[tuple[TableSegment, ...], Field(min_length=1)]
    cells: Annotated[tuple[TableCell, ...], Field(min_length=1)]
    caption_block_ids: tuple[BlockId, ...]
    header_row_indices: tuple[NonNegativeInt, ...]
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    confidence: Confidence | None
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_table_structure(self) -> Self:
        segments = {segment.segment_id: segment for segment in self.segments}
        if len(segments) != len(self.segments):
            raise ValueError("table segment_id values must be unique")
        cells = {cell.cell_id: cell for cell in self.cells}
        if len(cells) != len(self.cells):
            raise ValueError("table cell_id values must be unique")

        self._validate_segments(segments)
        self._validate_cells(segments)
        self._validate_headers()
        return self

    def _validate_segments(self, segments: dict[TableSegmentId, TableSegment]) -> None:
        for segment in self.segments:
            if segment.row_end_exclusive > self.logical_row_count:
                raise ValueError("table segment row range exceeds logical_row_count")
            previous_id = segment.continued_from_segment_id
            next_id = segment.continues_to_segment_id
            if previous_id is not None:
                previous = segments.get(previous_id)
                if previous is None or previous.continues_to_segment_id != segment.segment_id:
                    raise ValueError("continued_from_segment_id must resolve reciprocally")
            if next_id is not None:
                following = segments.get(next_id)
                if following is None or following.continued_from_segment_id != segment.segment_id:
                    raise ValueError("continues_to_segment_id must resolve reciprocally")

    def _validate_cells(self, segments: dict[TableSegmentId, TableSegment]) -> None:
        for cell in self.cells:
            row_end = cell.row_index + cell.row_span
            column_end = cell.column_index + cell.column_span
            if row_end > self.logical_row_count or column_end > self.logical_column_count:
                raise ValueError("table cell span exceeds logical table dimensions")

            if cell.fragments:
                for fragment in cell.fragments:
                    segment = segments.get(fragment.segment_id)
                    if segment is None:
                        raise ValueError("table cell fragment segment_id does not resolve")
                    if fragment.page_number != segment.page_number:
                        raise ValueError("table cell fragment page must match its segment")
                if cell.page_number not in {fragment.page_number for fragment in cell.fragments}:
                    raise ValueError("table cell anchor page must be represented by a fragment")

    def _validate_headers(self) -> None:
        if len(set(self.header_row_indices)) != len(self.header_row_indices):
            raise ValueError("header_row_indices must be unique")
        if tuple(sorted(self.header_row_indices)) != self.header_row_indices:
            raise ValueError("header_row_indices must be ordered")
        if any(index >= self.logical_row_count for index in self.header_row_indices):
            raise ValueError("header row index exceeds logical_row_count")

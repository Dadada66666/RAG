"""Minimal development Golden Dataset and benchmark contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from docparser.ir.base import NonNegativeInt, PageNumber, PositiveInt, StrictIRModel
from docparser.ir.enums import BlockType
from docparser.ir.geometry import BBox
from docparser.ir.types import NfcString, NonEmptyNfcString, Sha256Digest


class DatasetSlice(StrEnum):
    BORN_DIGITAL = "born-digital"
    IMAGE_ONLY_SCAN = "image-only-scan"
    OCR_LAYER_SCAN = "ocr-layer-scan"
    CHINESE = "chinese"
    ENGLISH = "english"
    BILINGUAL = "bilingual"
    TWO_COLUMN = "two-column"
    ROTATED = "rotated"
    CROPPED = "cropbox-differs"
    SIMPLE_TABLE = "simple-table"
    MERGED_CELLS = "merged-cells"
    FINANCIAL_TABLE = "financial-table"
    CROSS_PAGE_TABLE = "cross-page-table"
    NOISY_SCAN = "noisy-scan"


class TextTruth(StrictIRModel):
    expected_text: NfcString


class LayoutBlockTruth(StrictIRModel):
    truth_id: NonEmptyNfcString
    block_type: BlockType
    text: NfcString | None = None
    bbox: BBox | None = None


class ReadingOrderPair(StrictIRModel):
    before_truth_id: NonEmptyNfcString
    after_truth_id: NonEmptyNfcString


class TableCellTruth(StrictIRModel):
    row_index: NonNegativeInt
    column_index: NonNegativeInt
    row_span: PositiveInt = 1
    column_span: PositiveInt = 1
    text: NfcString
    is_header: bool = False


class TableTruth(StrictIRModel):
    logical_rows: PositiveInt
    logical_columns: PositiveInt
    cells: tuple[TableCellTruth, ...]


class CriticalNumericTruth(StrictIRModel):
    value: NonEmptyNfcString
    table_row: NonNegativeInt | None = None
    table_column: NonNegativeInt | None = None


class PageAnnotation(StrictIRModel):
    page_number: PageNumber
    text: TextTruth | None = None
    layout_blocks: tuple[LayoutBlockTruth, ...] = ()
    reading_order_pairs: tuple[ReadingOrderPair, ...] = ()
    tables: tuple[TableTruth, ...] = ()
    critical_numerics: tuple[CriticalNumericTruth, ...] = ()

    @model_validator(mode="after")
    def _validate_truth_references(self) -> PageAnnotation:
        truth_ids = {block.truth_id for block in self.layout_blocks}
        if any(
            pair.before_truth_id not in truth_ids or pair.after_truth_id not in truth_ids
            for pair in self.reading_order_pairs
        ):
            raise ValueError("reading-order pair must reference declared layout truth IDs")
        return self


class GoldenDocument(StrictIRModel):
    document_id: NonEmptyNfcString
    local_path: Path
    source_sha256: Sha256Digest
    slices: tuple[DatasetSlice, ...]
    annotations: tuple[PageAnnotation, ...]
    enabled: bool = True


class GoldenDatasetManifest(StrictIRModel):
    dataset_id: NonEmptyNfcString
    version: NonEmptyNfcString
    target_page_count_min: PositiveInt = 20
    target_page_count_max: PositiveInt = 50
    documents: tuple[GoldenDocument, ...]


class MetricValues(StrictIRModel):
    page_completeness: float = Field(strict=True, ge=0.0, le=1.0)
    text_edit_similarity: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    reading_order_pair_accuracy: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    table_detection_count_expected: NonNegativeInt
    table_detection_count_actual: NonNegativeInt
    logical_row_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    logical_column_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    cell_exact_text_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    rowspan_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    colspan_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    critical_numeric_exact_accuracy: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    resolvable_block_provenance: float = Field(strict=True, ge=0.0, le=1.0)
    exact_region_provenance: NonNegativeInt
    parent_region_provenance: NonNegativeInt
    page_only_provenance: NonNegativeInt
    elapsed_seconds: float = Field(strict=True, ge=0.0)
    pages_per_second: float = Field(strict=True, ge=0.0)
    runtime_device: NonEmptyNfcString


class BenchmarkCaseResult(StrictIRModel):
    document_id: NonEmptyNfcString
    parser_profile: NonEmptyNfcString
    slices: tuple[DatasetSlice, ...]
    metrics: MetricValues


class BenchmarkFailure(StrictIRModel):
    document_id: NonEmptyNfcString
    parser_profile: NonEmptyNfcString
    message: NonEmptyNfcString


class SliceParserSummary(StrictIRModel):
    parser_profile: NonEmptyNfcString
    case_count: PositiveInt
    text_edit_similarity: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    reading_order_pair_accuracy: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    cell_exact_text_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    critical_numeric_exact_accuracy: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    resolvable_block_provenance: float = Field(strict=True, ge=0.0, le=1.0)
    average_elapsed_seconds: float = Field(strict=True, ge=0.0)


class SliceComparison(StrictIRModel):
    slice: DatasetSlice
    parsers: tuple[SliceParserSummary, ...]


class ParsingBenchmarkReport(StrictIRModel):
    dataset_id: NonEmptyNfcString
    dataset_version: NonEmptyNfcString
    results: tuple[BenchmarkCaseResult, ...]
    failures: tuple[BenchmarkFailure, ...]
    skipped_missing_documents: tuple[NonEmptyNfcString, ...]
    slice_comparisons: tuple[SliceComparison, ...]
    recommendation: NonEmptyNfcString

"""Project Golden Dataset and mathematically explicit benchmark contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Self

from pydantic import Field, field_validator, model_validator

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


class DatasetMetricFamily(StrEnum):
    PROJECT_GOLDEN = "PROJECT_GOLDEN_DATASET_METRIC"
    PARSEBENCH_DERIVED_PROJECT = "PROJECT_METRICS_ON_PARSEBENCH_DERIVED_SUBSET"


class DatasetSplit(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    CALIBRATION = "CALIBRATION"
    PROTECTED_HOLDOUT = "PROTECTED_HOLDOUT"


class BenchmarkOutputStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARSER_FAILED = "PARSER_FAILED"
    METRIC_INCOMPLETE = "METRIC_INCOMPLETE"
    MISSING_INPUT = "MISSING_INPUT"


class MetricStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


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


class TableSegmentTruth(StrictIRModel):
    page_number: PageNumber
    bbox: BBox | None = None


class TableCellTruth(StrictIRModel):
    cell_id: NonEmptyNfcString
    row_index: NonNegativeInt
    column_index: NonNegativeInt
    row_span: PositiveInt = 1
    column_span: PositiveInt = 1
    text: NfcString
    is_header: bool = False
    page_number: PageNumber | None = None
    bbox: BBox | None = None


class TableTruth(StrictIRModel):
    truth_table_id: NonEmptyNfcString
    logical_table_id: NonEmptyNfcString | None = None
    logical_rows: PositiveInt
    logical_columns: PositiveInt
    page_segments: tuple[TableSegmentTruth, ...] = Field(min_length=1)
    cells: tuple[TableCellTruth, ...]
    caption: NfcString | None = None

    @model_validator(mode="after")
    def _validate_grid(self) -> Self:
        pages = [segment.page_number for segment in self.page_segments]
        if len(set(pages)) != len(pages) or pages != sorted(pages):
            raise ValueError("table truth page segments must be ordered and unique")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(set(cell_ids)) != len(cell_ids):
            raise ValueError("table truth cell_id values must be unique")
        anchors: set[tuple[int, int]] = set()
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            anchor = (cell.row_index, cell.column_index)
            if anchor in anchors:
                raise ValueError("table truth logical cell anchors must be unique")
            anchors.add(anchor)
            if (
                cell.row_index + cell.row_span > self.logical_rows
                or cell.column_index + cell.column_span > self.logical_columns
            ):
                raise ValueError("table truth cell span exceeds logical dimensions")
            for row in range(cell.row_index, cell.row_index + cell.row_span):
                for column in range(cell.column_index, cell.column_index + cell.column_span):
                    position = (row, column)
                    if position in occupied:
                        raise ValueError("table truth cells must not overlap")
                    occupied.add(position)
        return self


class CriticalNumericTruth(StrictIRModel):
    truth_id: NonEmptyNfcString
    value: NonEmptyNfcString
    multiplicity: PositiveInt = 1
    table_id: NonEmptyNfcString | None = None
    row_index: NonNegativeInt | None = None
    column_index: NonNegativeInt | None = None
    cell_id: NonEmptyNfcString | None = None
    currency: NfcString | None = None
    unit: NfcString | None = None

    @model_validator(mode="after")
    def _validate_location(self) -> Self:
        structural = any(
            value is not None
            for value in (self.table_id, self.row_index, self.column_index, self.cell_id)
        )
        if not structural:
            return self
        if self.table_id is None:
            raise ValueError("structural numeric truth requires table_id")
        coordinates_complete = self.row_index is not None and self.column_index is not None
        if self.cell_id is None and not coordinates_complete:
            raise ValueError("structural numeric truth requires cell_id or row/column")
        if (self.row_index is None) != (self.column_index is None):
            raise ValueError("structural numeric row/column must be supplied together")
        return self


class PageAnnotation(StrictIRModel):
    page_number: PageNumber
    text: TextTruth | None = None
    layout_blocks: tuple[LayoutBlockTruth, ...] = ()
    reading_order_pairs: tuple[ReadingOrderPair, ...] = ()
    tables: tuple[TableTruth, ...] = ()
    critical_numerics: tuple[CriticalNumericTruth, ...] = ()

    @model_validator(mode="after")
    def _validate_truth_references(self) -> Self:
        truth_ids = {block.truth_id for block in self.layout_blocks}
        if any(
            pair.before_truth_id not in truth_ids or pair.after_truth_id not in truth_ids
            for pair in self.reading_order_pairs
        ):
            raise ValueError("reading-order pair must reference declared layout truth IDs")
        table_ids = {table.truth_table_id for table in self.tables}
        if len(table_ids) != len(self.tables):
            raise ValueError("truth_table_id values must be unique within an annotation")
        for table in self.tables:
            if self.page_number not in {segment.page_number for segment in table.page_segments}:
                raise ValueError("annotation page must be represented by each nested table truth")
        if any(
            numeric.table_id is not None and numeric.table_id not in table_ids
            for numeric in self.critical_numerics
        ):
            raise ValueError("structural numeric table_id must resolve in the page annotation")
        return self


class GoldenDocument(StrictIRModel):
    document_id: NonEmptyNfcString
    source_document_id: NonEmptyNfcString | None = None
    local_path: Path
    source_sha256: Sha256Digest
    slices: tuple[DatasetSlice, ...]
    annotations: tuple[PageAnnotation, ...]
    enabled: bool = True

    @field_validator("local_path")
    @classmethod
    def _relative_local_path(cls, value: Path) -> Path:
        path = PurePosixPath(value.as_posix())
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Golden local_path must be relative and must not escape dataset root")
        return value


class GoldenDatasetManifest(StrictIRModel):
    dataset_id: NonEmptyNfcString
    version: NonEmptyNfcString
    metric_family: DatasetMetricFamily = DatasetMetricFamily.PROJECT_GOLDEN
    split: DatasetSplit = DatasetSplit.DEVELOPMENT
    source_revision: NonEmptyNfcString | None = None
    manifest_digest: Sha256Digest | None = None
    target_page_count_min: PositiveInt = 20
    target_page_count_max: PositiveInt = 80
    documents: tuple[GoldenDocument, ...]


class EvaluationDenominators(StrictIRModel):
    documents: PositiveInt = 1
    pages: NonNegativeInt
    text_pages: NonNegativeInt
    reading_order_pairs: NonNegativeInt
    tables: NonNegativeInt
    cells: NonNegativeInt
    numeric_annotations: NonNegativeInt
    structural_numeric_annotations: NonNegativeInt


class MetricValues(StrictIRModel):
    pages_expected: NonNegativeInt
    pages_present: NonNegativeInt
    page_completeness: float = Field(strict=True, ge=0.0, le=1.0)
    text_metric_status: MetricStatus
    text_edit_similarity: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    text_pages_expected: NonNegativeInt
    text_pages_scored: NonNegativeInt
    text_scored_characters: NonNegativeInt
    text_incomplete_reason: NfcString | None = None
    reading_order_pairs_correct: NonNegativeInt
    reading_order_pairs_expected: NonNegativeInt
    reading_order_pair_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    table_detection_tp: NonNegativeInt
    table_detection_fp: NonNegativeInt
    table_detection_fn: NonNegativeInt
    table_detection_precision: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    table_detection_recall: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    table_detection_f1: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    logical_rows_correct: NonNegativeInt
    logical_rows_expected: NonNegativeInt
    logical_row_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    logical_columns_correct: NonNegativeInt
    logical_columns_expected: NonNegativeInt
    logical_column_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    cells_text_correct: NonNegativeInt
    cells_expected: NonNegativeInt
    unexpected_cells: NonNegativeInt
    cell_exact_text_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    rowspans_correct: NonNegativeInt
    rowspans_expected: NonNegativeInt
    rowspan_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    colspans_correct: NonNegativeInt
    colspans_expected: NonNegativeInt
    colspan_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    occupied_grids_valid: NonNegativeInt
    occupied_grids_expected: NonNegativeInt
    occupied_grid_validity: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    table_segments_covered: NonNegativeInt
    table_segments_expected: NonNegativeInt
    table_segment_coverage: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    continuations_correct: NonNegativeInt
    continuations_expected: NonNegativeInt
    continuation_identity_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    page_numeric_presence_correct: NonNegativeInt
    page_numeric_presence_expected: NonNegativeInt
    page_numeric_presence_accuracy: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    structural_numerics_correct: NonNegativeInt
    structural_numerics_expected: NonNegativeInt
    critical_numeric_structural_exact_accuracy: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    resolvable_block_provenance: float = Field(strict=True, ge=0.0, le=1.0)
    exact_region_provenance: NonNegativeInt
    parent_region_provenance: NonNegativeInt
    page_only_provenance: NonNegativeInt
    eligible_retrieval_blocks: NonNegativeInt
    section_assigned_blocks: NonNegativeInt
    section_assignment_coverage: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    reading_order_pages_expected: NonNegativeInt
    reading_order_pages_resolved: NonNegativeInt
    resolved_reading_order_page_rate: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    elapsed_seconds: float = Field(strict=True, ge=0.0)
    pages_per_second: float = Field(strict=True, ge=0.0)
    runtime_device: NonEmptyNfcString


class BenchmarkExecutionMetadata(StrictIRModel):
    parser_name: NonEmptyNfcString
    parser_version: NonEmptyNfcString
    adapter_version: NonEmptyNfcString
    model_identifiers: tuple[NonEmptyNfcString, ...]
    pipeline_version: NonEmptyNfcString
    normalizer_version: NonEmptyNfcString
    config_digest: Sha256Digest


class BenchmarkCaseResult(StrictIRModel):
    document_id: NonEmptyNfcString
    parser_profile: NonEmptyNfcString
    slices: tuple[DatasetSlice, ...]
    output_status: BenchmarkOutputStatus
    denominators: EvaluationDenominators
    execution: BenchmarkExecutionMetadata | None = None
    metrics: MetricValues | None
    error_message: NfcString | None = None

    @model_validator(mode="after")
    def _validate_status_payload(self) -> Self:
        if self.output_status in {
            BenchmarkOutputStatus.SUCCESS,
            BenchmarkOutputStatus.METRIC_INCOMPLETE,
        }:
            if self.metrics is None:
                raise ValueError("successful or metric-incomplete case requires metrics")
        elif self.metrics is not None:
            raise ValueError("failed or missing-input case must not declare metrics")
        if self.output_status is BenchmarkOutputStatus.SUCCESS and self.error_message is not None:
            raise ValueError("successful case must not declare error_message")
        return self


class BenchmarkFailure(StrictIRModel):
    document_id: NonEmptyNfcString
    parser_profile: NonEmptyNfcString
    output_status: BenchmarkOutputStatus
    message: NonEmptyNfcString


class SliceParserSummary(StrictIRModel):
    parser_profile: NonEmptyNfcString
    case_count: PositiveInt
    documents: PositiveInt
    pages: NonNegativeInt
    tables: NonNegativeInt
    cells: NonNegativeInt
    numeric_annotations: NonNegativeInt
    successful_outputs: NonNegativeInt
    failed_outputs: NonNegativeInt
    metric_incomplete_outputs: NonNegativeInt
    output_coverage: float = Field(strict=True, ge=0.0, le=1.0)
    page_completeness_document_macro: float = Field(strict=True, ge=0.0, le=1.0)
    page_completeness_page_macro: float = Field(strict=True, ge=0.0, le=1.0)
    text_edit_similarity_document_macro: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    text_edit_similarity_page_macro: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    reading_order_pair_accuracy_micro: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    table_detection_precision_micro: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    table_detection_recall_micro: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    cell_exact_text_accuracy_micro: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    page_numeric_presence_accuracy_micro: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    critical_numeric_structural_exact_accuracy_micro: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    resolvable_block_provenance_document_macro: float = Field(strict=True, ge=0.0, le=1.0)
    average_elapsed_seconds: float = Field(strict=True, ge=0.0)


class SliceComparison(StrictIRModel):
    slice: DatasetSlice
    parsers: tuple[SliceParserSummary, ...]


class ParsingBenchmarkReport(StrictIRModel):
    dataset_id: NonEmptyNfcString
    dataset_version: NonEmptyNfcString
    metric_family: DatasetMetricFamily
    split: DatasetSplit
    manifest_digest: Sha256Digest
    metric_implementation_version: NonEmptyNfcString
    text_assembly_profile: NonEmptyNfcString
    results: tuple[BenchmarkCaseResult, ...]
    failures: tuple[BenchmarkFailure, ...]
    slice_comparisons: tuple[SliceComparison, ...]
    benchmark_complete: bool
    recommendation: NonEmptyNfcString
    accuracy_claim_status: NonEmptyNfcString

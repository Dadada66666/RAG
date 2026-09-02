"""Pinned ParseBench interoperability and subset manifest models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from docparser.ir.base import NonNegativeInt, PositiveInt, StrictIRModel
from docparser.ir.types import BoundedJsonObject, NfcString, NonEmptyNfcString, Sha256Digest

PARSEBENCH_REPOSITORY = "https://github.com/run-llama/ParseBench.git"
PARSEBENCH_COMMIT = "a9d1391da8a9e83c0a6c56a65ea994574ff43098"
PARSEBENCH_DATASET_REPOSITORY = "llamaindex/ParseBench"
PARSEBENCH_DATASET_REVISION = "57fb218011ac95a628ddefacecda8010343ca0a6"
PARSEBENCH_EVALUATOR_VERSION = "0.2.0"
PARSEBENCH_ADAPTER_VERSION = "parsebench-export@1.0.0"
PARSEBENCH_SELECTION_VERSION = "parsebench-complex-selector@1.0.0"


class ParseBenchPageIR(StrictIRModel):
    page_index: NonNegativeInt
    markdown: NfcString


class ParseBenchLayoutSegmentIR(StrictIRModel):
    x: float
    y: float
    w: float
    h: float
    confidence: float | None = None
    label: NfcString | None = None
    start_index: NonNegativeInt | None = None
    end_index: NonNegativeInt | None = None


class ParseBenchLayoutItemIR(StrictIRModel):
    type: NonEmptyNfcString = "text"
    md: NfcString = ""
    html: NfcString = ""
    value: NfcString = ""
    bbox: ParseBenchLayoutSegmentIR | None = None
    layout_segments: tuple[ParseBenchLayoutSegmentIR, ...] = ()


class ParseBenchLayoutPageIR(StrictIRModel):
    page_number: PositiveInt
    width: float | None = None
    height: float | None = None
    md: NfcString = ""
    text: NfcString = ""
    page_header_markdown: NfcString = ""
    page_footer_markdown: NfcString = ""
    printed_page_number: NfcString = ""
    original_orientation_angle: int | None = None
    items: tuple[ParseBenchLayoutItemIR, ...] = ()


class ParseBenchParseOutput(StrictIRModel):
    task_type: Literal["parse"] = "parse"
    example_id: NonEmptyNfcString
    pipeline_name: NonEmptyNfcString
    pages: tuple[ParseBenchPageIR, ...]
    layout_pages: tuple[ParseBenchLayoutPageIR, ...]
    markdown: NfcString
    job_id: NfcString | None = None


class ParseBenchInferenceRequest(StrictIRModel):
    example_id: NonEmptyNfcString
    source_file_path: NonEmptyNfcString
    product_type: Literal["parse"] = "parse"
    schema_override: BoundedJsonObject | None = None
    config_override: BoundedJsonObject | None = None


class ParseBenchInferenceResult(StrictIRModel):
    request: ParseBenchInferenceRequest
    pipeline_name: NonEmptyNfcString
    product_type: Literal["parse"] = "parse"
    raw_output: BoundedJsonObject
    output: ParseBenchParseOutput
    started_at: NonEmptyNfcString
    completed_at: NonEmptyNfcString
    latency_in_ms: NonNegativeInt


class ParseBenchRunRequest(StrictIRModel):
    benchmark_id: NonEmptyNfcString
    subset_id: NonEmptyNfcString
    subset_manifest_digest: Sha256Digest
    checkout_path: Path
    evaluator_command: tuple[NonEmptyNfcString, ...] = Field(min_length=1)
    official_result_path: Path
    dataset_root: Path
    export_root: Path
    environment_digest: Sha256Digest
    hardware_description: NonEmptyNfcString
    evaluator_version: NonEmptyNfcString = PARSEBENCH_EVALUATOR_VERSION
    dataset_revision: NonEmptyNfcString = PARSEBENCH_DATASET_REVISION
    repository_commit: NonEmptyNfcString = PARSEBENCH_COMMIT


class OfficialParseBenchResult(StrictIRModel):
    terminology: Literal["OFFICIAL_PARSEBENCH_METRIC"] = "OFFICIAL_PARSEBENCH_METRIC"
    repository: NonEmptyNfcString = PARSEBENCH_REPOSITORY
    repository_commit: NonEmptyNfcString
    dataset_repository: NonEmptyNfcString = PARSEBENCH_DATASET_REPOSITORY
    dataset_revision: NonEmptyNfcString
    benchmark_id: NonEmptyNfcString
    subset_id: NonEmptyNfcString
    subset_manifest_digest: Sha256Digest
    evaluator_version: NonEmptyNfcString
    evaluator_command: tuple[NonEmptyNfcString, ...]
    adapter_version: NonEmptyNfcString = PARSEBENCH_ADAPTER_VERSION
    environment_digest: Sha256Digest
    hardware_description: NonEmptyNfcString
    official_result_digest: Sha256Digest
    official_metrics: BoundedJsonObject


class ParseBenchStratum(StrEnum):
    HARD_TABLE = "hard-table"
    MERGED_CELLS = "merged-cells"
    OCR_SCAN = "ocr-scan"
    MULTICOLUMN = "multicolumn"
    DIFFICULT_LAYOUT = "difficult-layout"
    NUMERIC_FINANCIAL = "numeric-financial"
    BILINGUAL_MULTILINGUAL = "bilingual-multilingual"


class ParseBenchCandidate(StrictIRModel):
    item_id: NonEmptyNfcString
    source_document_id: NonEmptyNfcString
    page_number: PositiveInt
    source_path: NonEmptyNfcString
    source_digest: Sha256Digest | None = None
    strata: tuple[ParseBenchStratum, ...] = Field(min_length=1)


class SubsetSelectionStatus(StrEnum):
    UNPROVISIONED = "UNPROVISIONED"
    FROZEN = "FROZEN"


class ParseBenchSubsetManifest(StrictIRModel):
    manifest_version: NonEmptyNfcString = "1.0.0"
    dataset_id: NonEmptyNfcString
    split: Literal["DEVELOPMENT", "PROTECTED_HOLDOUT"]
    selection_status: SubsetSelectionStatus
    upstream_repository: NonEmptyNfcString = PARSEBENCH_DATASET_REPOSITORY
    upstream_revision: NonEmptyNfcString = PARSEBENCH_DATASET_REVISION
    selection_script_version: NonEmptyNfcString = PARSEBENCH_SELECTION_VERSION
    seed: NonNegativeInt
    target_count: PositiveInt
    candidate_id_ordering: NonEmptyNfcString = "sha256(seed:item_id),item_id"
    selected_items: tuple[ParseBenchCandidate, ...]
    selected_item_digest: Sha256Digest
    access_policy: NonEmptyNfcString

    @model_validator(mode="after")
    def _validate_selection(self) -> Self:
        item_ids = [item.item_id for item in self.selected_items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("ParseBench subset item IDs must be unique")
        source_pages = [(item.source_document_id, item.page_number) for item in self.selected_items]
        if len(set(source_pages)) != len(source_pages):
            raise ValueError("ParseBench subset source pages must be unique")
        if self.selection_status is SubsetSelectionStatus.FROZEN:
            if len(self.selected_items) != self.target_count:
                raise ValueError("frozen subset must contain target_count items")
        elif self.selected_items:
            raise ValueError("unprovisioned subset must not contain selected items")
        return self

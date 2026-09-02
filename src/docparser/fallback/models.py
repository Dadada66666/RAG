"""MVP page/table fallback planning and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from docparser.application.parsing import ParseDiagnostics
from docparser.ir.base import StrictIRModel
from docparser.ir.geometry import BBox, Rotation
from docparser.ir.ids import DocumentId
from docparser.ir.models import DocumentIR
from docparser.ir.types import NonEmptyNfcString, Sha256Digest
from docparser.quality import QualityDecision, QualityReport, QualityTarget


class FallbackTargetStatus(StrEnum):
    APPLIED = "APPLIED"
    REJECTED_NO_CLEAR_GAIN = "REJECTED_NO_CLEAR_GAIN"
    REJECTED_UNSUPPORTED_DEPENDENCY = "REJECTED_UNSUPPORTED_DEPENDENCY"
    CONFLICT = "CONFLICT"
    UNSUPPORTED_CROSS_PAGE_TABLE_FALLBACK = "UNSUPPORTED_CROSS_PAGE_TABLE_FALLBACK"
    PARSER_FAILED = "PARSER_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class FallbackBudget(StrictIRModel):
    max_rounds: int = Field(default=1, strict=True, ge=1, le=1)
    max_targets: int = Field(default=3, strict=True, ge=1)
    max_page_fraction: float = Field(default=0.1, strict=True, gt=0.0, le=1.0)


class FallbackProfile(StrictIRModel):
    profile_id: NonEmptyNfcString
    evidence_dataset_digest: Sha256Digest
    created_from_commit: NonEmptyNfcString
    primary_profile: NonEmptyNfcString
    alternate_profile: NonEmptyNfcString
    supported_slice: NonEmptyNfcString
    eligible_rule_ids: tuple[NonEmptyNfcString, ...]
    minimum_candidate_match: float = Field(strict=True, ge=0.0, le=1.0)
    winner_margin: float = Field(strict=True, ge=0.0, le=1.0)
    budget: FallbackBudget = Field(default_factory=FallbackBudget)
    frozen: bool

    @model_validator(mode="after")
    def _validate_unique_rules(self) -> Self:
        if len(set(self.eligible_rule_ids)) != len(self.eligible_rule_ids):
            raise ValueError("eligible_rule_ids must be unique")
        if self.frozen and not self.eligible_rule_ids:
            raise ValueError("frozen fallback profile requires eligible_rule_ids")
        return self


class PlannedFallbackTarget(StrictIRModel):
    target: QualityTarget
    triggering_rule_ids: tuple[NonEmptyNfcString, ...]
    attempt_fingerprint: Sha256Digest


class FallbackPlan(StrictIRModel):
    enabled: bool
    reason: NonEmptyNfcString
    targets: tuple[PlannedFallbackTarget, ...]
    max_rounds: int = Field(strict=True, ge=1, le=1)


class FallbackTargetResult(StrictIRModel):
    target: QualityTarget
    status: FallbackTargetStatus
    attempt_fingerprint: Sha256Digest
    alternate_profile: NonEmptyNfcString
    detail: NonEmptyNfcString


class FallbackResult(StrictIRModel):
    attempted: int = Field(strict=True, ge=0)
    applied: int = Field(strict=True, ge=0)
    results: tuple[FallbackTargetResult, ...]


class RobustDiagnostics(StrictIRModel):
    mode: NonEmptyNfcString
    calibration_profile: NonEmptyNfcString | None
    fallback_profile: NonEmptyNfcString | None
    fallback_attempts: int = Field(strict=True, ge=0)
    fallback_applied: int = Field(strict=True, ge=0)
    baseline_parser_diagnostics: ParseDiagnostics


@dataclass(frozen=True, slots=True)
class MaterializedPage:
    source_document_id: DocumentId
    source_page_number: int
    temporary_pdf: Path
    media_box: BBox
    crop_box: BBox
    rotation: Rotation
    width: float
    height: float
    digest: Sha256Digest


@dataclass(frozen=True, slots=True)
class CandidatePage:
    original_page_number: int
    document: DocumentIR


@dataclass(frozen=True, slots=True)
class RobustParseOutcome:
    baseline_document: DocumentIR
    baseline_quality_report: QualityReport
    fallback_plan: FallbackPlan | None
    fallback_result: FallbackResult | None
    final_document: DocumentIR
    final_quality_report: QualityReport
    final_decision: QualityDecision
    diagnostics: RobustDiagnostics

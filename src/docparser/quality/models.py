"""Typed evidence, policy, and calibration contracts for the quality gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from docparser.ir.base import StrictIRModel
from docparser.ir.ids import DocumentId, QualityReportId, RevisionId, TableId
from docparser.ir.models import DocumentIR
from docparser.ir.types import BoundedJsonObject, NonEmptyNfcString, Sha256Digest, UtcTimestamp
from docparser.preflight import DocumentProfile


class QualityDecision(StrEnum):
    ACCEPT = "ACCEPT"
    FALLBACK_REQUIRED = "FALLBACK_REQUIRED"
    REJECT = "REJECT"


class QualityMode(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    CALIBRATION = "CALIBRATION"
    CALIBRATED = "CALIBRATED"


class RuleAction(StrEnum):
    ADVISORY = "ADVISORY"
    FALLBACK = "FALLBACK"
    REJECT = "REJECT"


class QualityScope(StrEnum):
    DOCUMENT = "DOCUMENT"
    PAGE = "PAGE"
    TABLE = "TABLE"


class AcceptanceUnit(StrEnum):
    DOCUMENT = "DOCUMENT"
    PAGE = "PAGE"
    TABLE = "TABLE"


class SignalKind(StrEnum):
    INTEGRITY = "INTEGRITY"
    DISAGREEMENT = "DISAGREEMENT"
    ANOMALY = "ANOMALY"
    UNCERTAINTY = "UNCERTAINTY"


class SignalSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SignalOutcome(StrEnum):
    CLEAR = "CLEAR"
    TRIGGERED = "TRIGGERED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PROVISIONAL = "PROVISIONAL"


class CompletenessThresholds(StrictIRModel):
    minimum_native_characters: int = Field(strict=True, ge=1)
    minimum_parser_to_native_ratio: float = Field(strict=True, gt=0.0, le=1.0)


class TableThresholds(StrictIRModel):
    maximum_empty_cell_ratio: float = Field(strict=True, ge=0.0, le=1.0)
    minimum_occupied_grid_ratio: float = Field(strict=True, ge=0.0, le=1.0)


class CalibrationProfile(StrictIRModel):
    profile_id: NonEmptyNfcString
    parser_profile: NonEmptyNfcString
    dataset_digest: Sha256Digest
    ruleset_version: NonEmptyNfcString
    supported_slices: tuple[NonEmptyNfcString, ...]
    completeness: CompletenessThresholds | None = None
    table: TableThresholds | None = None
    rule_actions: dict[str, RuleAction]
    frozen: bool
    created_from_commit: NonEmptyNfcString
    calibration_report_digest: Sha256Digest | None = None
    calibration_sample_count: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def _validate_frozen_profile(self) -> Self:
        if len(set(self.supported_slices)) != len(self.supported_slices):
            raise ValueError("supported_slices must be unique")
        if (self.calibration_report_digest is None) != (self.calibration_sample_count is None):
            raise ValueError("calibration evidence digest and sample count must be set together")
        if not self.frozen:
            return self
        if self.calibration_report_digest is None or self.calibration_sample_count is None:
            raise ValueError("frozen profile requires calibration report evidence")
        if not self.supported_slices:
            raise ValueError("frozen profile requires at least one supported slice")
        if (
            self.rule_actions.get("COMPLETENESS.SOURCE_RICH_PARSE_SPARSE")
            in {RuleAction.FALLBACK, RuleAction.REJECT}
            and self.completeness is None
        ):
            raise ValueError("blocking completeness rule requires calibrated thresholds")
        if (
            self.rule_actions.get("TABLE.DEGENERATE_STRUCTURE")
            in {RuleAction.FALLBACK, RuleAction.REJECT}
            and self.table is None
        ):
            raise ValueError("blocking table rule requires calibrated thresholds")
        return self


class QualityTarget(StrictIRModel):
    scope: QualityScope
    page_number: int | None = Field(default=None, strict=True, ge=1)
    table_id: TableId | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        if self.scope is QualityScope.DOCUMENT:
            if self.page_number is not None or self.table_id is not None:
                raise ValueError("DOCUMENT target cannot declare page or table")
        elif self.scope is QualityScope.PAGE:
            if self.page_number is None or self.table_id is not None:
                raise ValueError("PAGE target requires only page_number")
        elif self.page_number is None or self.table_id is None:
            raise ValueError("TABLE target requires page_number and table_id")
        return self


class QualitySignal(StrictIRModel):
    rule_id: NonEmptyNfcString
    signal_kind: SignalKind
    severity: SignalSeverity
    outcome: SignalOutcome
    target: QualityTarget
    predicted_failure_type: NonEmptyNfcString
    action: RuleAction
    calibrated: bool
    evidence: BoundedJsonObject
    message: NonEmptyNfcString

    @property
    def is_triggered(self) -> bool:
        return self.outcome is SignalOutcome.TRIGGERED

    @property
    def is_blocking(self) -> bool:
        return self.is_triggered and self.action in {RuleAction.FALLBACK, RuleAction.REJECT}


class QualityReport(StrictIRModel):
    quality_report_id: QualityReportId
    document_id: DocumentId
    revision_id: RevisionId
    ruleset_version: NonEmptyNfcString
    calibration_profile_id: NonEmptyNfcString | None
    mode: QualityMode
    decision: QualityDecision
    signals: tuple[QualitySignal, ...]
    fallback_targets: tuple[QualityTarget, ...]
    calibration_required: bool
    created_at: UtcTimestamp

    @property
    def blocking_signals(self) -> tuple[QualitySignal, ...]:
        return tuple(signal for signal in self.signals if signal.is_blocking)


@dataclass(frozen=True, slots=True)
class ValidationRequest:
    document: DocumentIR
    profile: DocumentProfile
    calibration: CalibrationProfile | None = None
    supported_slice: str | None = None


def quality_mode_for_request(request: ValidationRequest) -> QualityMode:
    """Resolve whether a profile may observe, measure, or control publication."""

    profile = request.calibration
    if profile is None or request.supported_slice is None:
        return QualityMode.OBSERVE_ONLY
    if (
        request.supported_slice not in profile.supported_slices
        and "*" not in profile.supported_slices
    ):
        return QualityMode.OBSERVE_ONLY
    return QualityMode.CALIBRATED if profile.frozen else QualityMode.CALIBRATION


class FailureLabel(StrictIRModel):
    rule_id: NonEmptyNfcString
    scope: QualityScope
    page_number: int | None = Field(default=None, strict=True, ge=1)
    table_id: TableId | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        QualityTarget(
            scope=self.scope,
            page_number=self.page_number,
            table_id=self.table_id,
        )
        return self

    @property
    def canonical_key(self) -> tuple[str, QualityScope, int | None, str | None]:
        return (
            str(self.rule_id),
            self.scope,
            self.page_number,
            str(self.table_id) if self.table_id is not None else None,
        )


class CalibrationTargetTruth(StrictIRModel):
    target: QualityTarget
    meets_acceptance_standard: bool


class CalibrationTruth(StrictIRModel):
    sample_id: NonEmptyNfcString
    target_truths: Annotated[tuple[CalibrationTargetTruth, ...], Field(min_length=1)]
    failure_labels: tuple[FailureLabel, ...] = ()

    @model_validator(mode="after")
    def _validate_failure_labels(self) -> Self:
        targets = [target_truth.target for target_truth in self.target_truths]
        if len(set(target.model_dump_json() for target in targets)) != len(targets):
            raise ValueError("target_truths must be unique")
        keys = [label.canonical_key for label in self.failure_labels]
        if len(set(keys)) != len(keys):
            raise ValueError("failure_labels must be unique")
        return self


class CalibrationSample(StrictIRModel):
    truth: CalibrationTruth
    report: QualityReport


class ConfusionMetrics(StrictIRModel):
    tp: int = Field(strict=True, ge=0)
    fp: int = Field(strict=True, ge=0)
    tn: int = Field(strict=True, ge=0)
    fn: int = Field(strict=True, ge=0)
    precision: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    false_positive_rate: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    false_negative_rate: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)


class RuleCalibrationMetrics(StrictIRModel):
    rule_id: NonEmptyNfcString
    acceptance_unit: AcceptanceUnit
    confusion: ConfusionMetrics


class SystemCalibrationMetrics(StrictIRModel):
    acceptance_unit: AcceptanceUnit
    samples: int = Field(strict=True, ge=0)
    accepted: int = Field(strict=True, ge=0)
    accepted_correct: int = Field(strict=True, ge=0)
    accepted_output_precision: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    coverage: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    fallback_rate: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)
    unresolved_failure_rate: float | None = Field(default=None, strict=True, ge=0.0, le=1.0)


class CalibrationReport(StrictIRModel):
    dataset_digest: Sha256Digest
    profile_id: NonEmptyNfcString
    rule_metrics: tuple[RuleCalibrationMetrics, ...]
    system_metrics: tuple[SystemCalibrationMetrics, ...]
    sample_count: int = Field(strict=True, ge=0)

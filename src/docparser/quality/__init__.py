"""Public Quality Gate API."""

from docparser.quality.calibration import evaluate_calibration, freeze_profile
from docparser.quality.engine import (
    QUALITY_RULESET_VERSION,
    DeterministicQualityGate,
    apply_quality_report,
)
from docparser.quality.models import (
    AcceptanceUnit,
    CalibrationProfile,
    CalibrationReport,
    CalibrationSample,
    CalibrationTruth,
    CompletenessThresholds,
    QualityDecision,
    QualityMode,
    QualityReport,
    QualityScope,
    QualitySignal,
    QualityTarget,
    RuleAction,
    SignalOutcome,
    TableThresholds,
    ValidationRequest,
)

__all__ = [
    "QUALITY_RULESET_VERSION",
    "AcceptanceUnit",
    "CalibrationProfile",
    "CalibrationReport",
    "CalibrationSample",
    "CalibrationTruth",
    "CompletenessThresholds",
    "DeterministicQualityGate",
    "QualityDecision",
    "QualityMode",
    "QualityReport",
    "QualityScope",
    "QualitySignal",
    "QualityTarget",
    "RuleAction",
    "SignalOutcome",
    "TableThresholds",
    "ValidationRequest",
    "apply_quality_report",
    "evaluate_calibration",
    "freeze_profile",
]

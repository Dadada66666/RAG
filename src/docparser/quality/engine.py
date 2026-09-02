"""Calibrated risk gate over Canonical IR and independent source evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from docparser.ir.content import IssueCounts, QualitySummary
from docparser.ir.enums import QualityStatus
from docparser.ir.ids import QualityReportId, generate_quality_report_id
from docparser.ir.models import DocumentIR
from docparser.ir.types import UtcTimestamp
from docparser.quality.models import (
    QualityDecision,
    QualityMode,
    QualityReport,
    QualityScope,
    QualitySignal,
    QualityTarget,
    RuleAction,
    SignalKind,
    SignalOutcome,
    SignalSeverity,
    ValidationRequest,
    quality_mode_for_request,
)
from docparser.quality.rules import DEFAULT_RULES, QualityRule

QUALITY_RULESET_VERSION = "quality-mvp@1.0.0"


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


def _integrity_code(message: str) -> str:
    lowered = message.lower()
    if (
        "len(pages)" in lowered
        or "page count" in lowered
        or ("pages" in lowered and "at least 1 item" in lowered)
    ):
        return "INTEGRITY.PAGE_COUNT_MISMATCH"
    if "pages must be ordered" in lowered or "page_number does not exist" in lowered:
        return "INTEGRITY.MISSING_PAGE"
    if "bbox" in lowered or "geometry" in lowered:
        return "INTEGRITY.INVALID_BBOX"
    if "provenance" in lowered:
        return "INTEGRITY.BROKEN_PROVENANCE"
    if "table" in lowered or "cell" in lowered or "grid" in lowered:
        return "TABLE.LOGICAL_OCCUPANCY_INVALID"
    return "INTEGRITY.INVALID_DOCUMENT"


def _integrity_signal(error: ValueError) -> QualitySignal:
    code = _integrity_code(str(error))
    return QualitySignal(
        rule_id=code,
        signal_kind=SignalKind.INTEGRITY,
        severity=SignalSeverity.CRITICAL,
        outcome=SignalOutcome.TRIGGERED,
        target=QualityTarget(scope=QualityScope.DOCUMENT),
        predicted_failure_type=code.removeprefix("INTEGRITY."),
        action=RuleAction.REJECT,
        calibrated=True,
        evidence={"invariant_error": str(error)},
        message="Canonical IR hard integrity validation failed.",
    )


class DeterministicQualityGate:
    """Run hard invariants and the calibrated MVP evidence rules."""

    def __init__(
        self,
        rules: Sequence[QualityRule] = DEFAULT_RULES,
        *,
        report_id_factory: Callable[[], QualityReportId] = generate_quality_report_id,
        clock: Callable[[], UtcTimestamp] = _utc_now,
    ) -> None:
        self._rules = tuple(rules)
        self._report_id_factory = report_id_factory
        self._clock = clock

    def evaluate(self, request: ValidationRequest) -> QualityReport:
        profile = request.calibration
        mode = quality_mode_for_request(request)
        signals: list[QualitySignal] = []
        try:
            DocumentIR.model_validate(request.document.model_dump(mode="python"))
        except ValueError as exc:
            signals.append(_integrity_signal(exc))
        if not signals:
            for rule in self._rules:
                signals.extend(rule.evaluate(request))

        triggered = [signal for signal in signals if signal.is_triggered]
        if any(signal.action is RuleAction.REJECT for signal in triggered):
            decision = QualityDecision.REJECT
        elif mode is QualityMode.OBSERVE_ONLY:
            decision = QualityDecision.REJECT
        elif any(signal.action is RuleAction.FALLBACK for signal in triggered):
            decision = QualityDecision.FALLBACK_REQUIRED
        else:
            decision = QualityDecision.ACCEPT

        unique_targets: list[QualityTarget] = []
        for signal in triggered:
            if signal.action is RuleAction.FALLBACK and signal.target not in unique_targets:
                unique_targets.append(signal.target)
        return QualityReport(
            quality_report_id=self._report_id_factory(),
            document_id=request.document.document_id,
            revision_id=request.document.revision_id,
            ruleset_version=(profile.ruleset_version if profile else QUALITY_RULESET_VERSION),
            calibration_profile_id=profile.profile_id if profile else None,
            mode=mode,
            decision=decision,
            signals=tuple(signals),
            fallback_targets=tuple(unique_targets),
            calibration_required=mode is not QualityMode.CALIBRATED,
            created_at=self._clock(),
        )


def apply_quality_report(document: DocumentIR, report: QualityReport) -> DocumentIR:
    """Return an evaluated IR view without fabricating a quality score."""

    if report.document_id != document.document_id:
        raise ValueError("quality report document_id does not match DocumentIR")
    counts = Counter(signal.severity.value for signal in report.signals if signal.is_triggered)
    if report.decision is QualityDecision.ACCEPT:
        status = QualityStatus.PASS
        publishable = report.mode is QualityMode.CALIBRATED
    elif report.decision is QualityDecision.FALLBACK_REQUIRED:
        status = QualityStatus.DEGRADED
        publishable = False
    else:
        status = QualityStatus.FAIL
        publishable = False
    return document.model_copy(
        update={
            "schema_version": "1.2.0",
            "processing": document.processing.model_copy(
                update={"validator_ruleset_version": report.ruleset_version}
            ),
            "quality_summary": QualitySummary(
                quality_report_id=report.quality_report_id,
                score=None,
                status=status,
                issue_counts=IssueCounts(
                    INFO=counts["INFO"],
                    WARNING=counts["WARNING"],
                    ERROR=counts["ERROR"],
                    CRITICAL=counts["CRITICAL"],
                ),
                publishable=publishable,
            ),
        }
    )

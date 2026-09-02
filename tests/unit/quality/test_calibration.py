from __future__ import annotations

from collections.abc import Iterable

import pytest
from pydantic import ValidationError
from tests.quality_factory import calibration_profile, quality_document, quality_profile

from docparser.ir.ids import QualityReportId, TableId
from docparser.ir.types import UtcTimestamp
from docparser.quality import (
    AcceptanceUnit,
    CalibrationProfile,
    CalibrationReport,
    CalibrationSample,
    CalibrationTruth,
    DeterministicQualityGate,
    FailureLabel,
    QualityDecision,
    QualityMode,
    QualityReport,
    QualityScope,
    QualitySignal,
    QualityTarget,
    RuleAction,
    SignalOutcome,
    ValidationRequest,
    calibration_report_digest,
    evaluate_calibration,
    freeze_profile,
)
from docparser.quality.models import RuleCalibrationMetrics, SignalKind, SignalSeverity

NUMERIC_RULE = "NUMERIC.NATIVE_PARSER_DISAGREEMENT"
ORDER_RULE = "ORDER.UNRESOLVED"
TABLE_RULE = "TABLE.DEGENERATE_STRUCTURE"
TABLE_A = TableId("tbl_643fa564-47f6-566d-9244-cc4076d51001")
TABLE_B = TableId("tbl_643fa564-47f6-566d-9244-cc4076d51002")


def _gate() -> DeterministicQualityGate:
    return DeterministicQualityGate(
        report_id_factory=lambda: QualityReportId("qrep_018bcfe5-6800-7000-8000-000000000098"),
        clock=lambda: UtcTimestamp("2026-09-02T08:00:00Z"),
    )


def _signal(
    rule_id: str,
    target: QualityTarget,
    *,
    triggered: bool,
) -> QualitySignal:
    return QualitySignal(
        rule_id=rule_id,
        signal_kind=SignalKind.ANOMALY,
        severity=SignalSeverity.ERROR,
        outcome=SignalOutcome.TRIGGERED if triggered else SignalOutcome.CLEAR,
        target=target,
        predicted_failure_type="TEST_FAILURE",
        action=RuleAction.FALLBACK,
        calibrated=False,
        evidence={},
        message="Calibration known-answer signal.",
    )


def _report(signals: Iterable[QualitySignal]) -> QualityReport:
    materialized = tuple(signals)
    decision = (
        QualityDecision.FALLBACK_REQUIRED
        if any(signal.is_blocking for signal in materialized)
        else QualityDecision.ACCEPT
    )
    document = quality_document()
    return QualityReport(
        quality_report_id=QualityReportId("qrep_018bcfe5-6800-7000-8000-000000000097"),
        document_id=document.document_id,
        revision_id=document.revision_id,
        ruleset_version="quality-test@1.0.0",
        calibration_profile_id="quality-test-v1",
        mode=QualityMode.CALIBRATION,
        decision=decision,
        signals=materialized,
        fallback_targets=tuple(
            signal.target for signal in materialized if signal.is_blocking
        ),
        calibration_required=True,
        created_at=UtcTimestamp("2026-09-02T08:00:00Z"),
    )


def _metric(
    report: CalibrationReport,
    rule_id: str,
    unit: AcceptanceUnit,
) -> RuleCalibrationMetrics:
    return next(
        metric
        for metric in report.rule_metrics
        if metric.rule_id == rule_id and metric.acceptance_unit is unit
    )


def test_calibration_profile_applies_policy_without_publishing() -> None:
    candidate = calibration_profile(frozen=False)
    report = _gate().evaluate(
        ValidationRequest(
            quality_document("Value 10"),
            quality_profile("Value 20"),
            candidate,
            "test",
        )
    )

    assert report.mode is QualityMode.CALIBRATION
    assert report.decision is QualityDecision.FALLBACK_REQUIRED
    assert report.calibration_required
    assert not any(signal.calibrated for signal in report.signals)


def test_scope_mismatch_counts_one_false_positive_and_one_false_negative() -> None:
    report = _report(
        (
            _signal(
                NUMERIC_RULE,
                QualityTarget(scope=QualityScope.PAGE, page_number=3),
                triggered=False,
            ),
            _signal(
                NUMERIC_RULE,
                QualityTarget(scope=QualityScope.PAGE, page_number=4),
                triggered=True,
            ),
        )
    )
    result = evaluate_calibration(
        calibration_profile(frozen=False),
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="wrong-page",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=False,
                    failure_labels=(
                        FailureLabel(
                            rule_id=NUMERIC_RULE,
                            scope=QualityScope.PAGE,
                            page_number=3,
                        ),
                    ),
                ),
                report=report,
            ),
        ),
    )
    numeric = _metric(result, NUMERIC_RULE, AcceptanceUnit.PAGE)

    assert numeric.confusion.tp == 0
    assert numeric.confusion.fp == 1
    assert numeric.confusion.fn == 1


def test_multiple_scoped_failure_labels_each_receive_true_positive_credit() -> None:
    report = _report(
        (
            _signal(
                NUMERIC_RULE,
                QualityTarget(scope=QualityScope.PAGE, page_number=2),
                triggered=True,
            ),
            _signal(
                ORDER_RULE,
                QualityTarget(scope=QualityScope.PAGE, page_number=2),
                triggered=True,
            ),
        )
    )
    labels = tuple(
        FailureLabel(rule_id=rule_id, scope=QualityScope.PAGE, page_number=2)
        for rule_id in (NUMERIC_RULE, ORDER_RULE)
    )
    result = evaluate_calibration(
        calibration_profile(frozen=False),
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="two-failures",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=False,
                    failure_labels=labels,
                ),
                report=report,
            ),
        ),
    )

    assert sum(
        metric.confusion.tp
        for metric in result.rule_metrics
        if metric.acceptance_unit is AcceptanceUnit.PAGE
    ) == 2


def test_page_system_metrics_isolate_one_failed_page_from_99_accepted_pages() -> None:
    signals = tuple(
        _signal(
            ORDER_RULE,
            QualityTarget(scope=QualityScope.PAGE, page_number=page_number),
            triggered=page_number == 100,
        )
        for page_number in range(1, 101)
    )
    result = evaluate_calibration(
        calibration_profile(frozen=False),
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="hundred-pages",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=False,
                    failure_labels=(
                        FailureLabel(
                            rule_id=ORDER_RULE,
                            scope=QualityScope.PAGE,
                            page_number=100,
                        ),
                    ),
                ),
                report=_report(signals),
            ),
        ),
    )
    page = next(
        metric
        for metric in result.system_metrics
        if metric.acceptance_unit is AcceptanceUnit.PAGE
    )

    assert page.samples == 100
    assert page.accepted == 99
    assert page.accepted_correct == 99
    assert page.coverage == 0.99
    assert page.fallback_rate == 0.01


def test_table_metrics_isolate_two_tables_on_the_same_page() -> None:
    result = evaluate_calibration(
        calibration_profile(frozen=False),
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="two-tables",
                    acceptance_unit=AcceptanceUnit.TABLE,
                    meets_acceptance_standard=False,
                    failure_labels=(
                        FailureLabel(
                            rule_id=TABLE_RULE,
                            scope=QualityScope.TABLE,
                            page_number=1,
                            table_id=TABLE_A,
                        ),
                    ),
                ),
                report=_report(
                    (
                        _signal(
                            TABLE_RULE,
                            QualityTarget(
                                scope=QualityScope.TABLE,
                                page_number=1,
                                table_id=TABLE_A,
                            ),
                            triggered=True,
                        ),
                        _signal(
                            TABLE_RULE,
                            QualityTarget(
                                scope=QualityScope.TABLE,
                                page_number=1,
                                table_id=TABLE_B,
                            ),
                            triggered=False,
                        ),
                    )
                ),
            ),
        ),
    )
    table = next(
        metric
        for metric in result.system_metrics
        if metric.acceptance_unit is AcceptanceUnit.TABLE
    )

    assert table.samples == 2
    assert table.accepted == 1
    assert table.fallback_rate == 0.5


def test_freeze_binds_matching_nonempty_calibration_evidence() -> None:
    profile = calibration_profile(frozen=False)
    good = _gate().evaluate(
        ValidationRequest(quality_document(), quality_profile(), profile, "test")
    )
    report = evaluate_calibration(
        profile,
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="good-page",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=True,
                ),
                report=good,
            ),
        ),
    )
    frozen = freeze_profile(profile, report)

    assert frozen.frozen
    assert frozen.calibration_report_digest == calibration_report_digest(report)
    assert frozen.calibration_sample_count == 1

    empty = evaluate_calibration(profile, ())
    with pytest.raises(ValueError, match="without calibration samples"):
        freeze_profile(profile, empty)

    payload = profile.model_dump(mode="python")
    payload["frozen"] = True
    with pytest.raises(ValidationError, match="calibration report evidence"):
        CalibrationProfile.model_validate(payload)

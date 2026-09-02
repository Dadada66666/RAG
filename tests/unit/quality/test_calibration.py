from __future__ import annotations

from tests.quality_factory import calibration_profile, quality_document, quality_profile

from docparser.ir.ids import QualityReportId
from docparser.ir.types import UtcTimestamp
from docparser.quality import (
    AcceptanceUnit,
    CalibrationSample,
    CalibrationTruth,
    DeterministicQualityGate,
    QualityDecision,
    ValidationRequest,
    evaluate_calibration,
    freeze_profile,
)


def _gate() -> DeterministicQualityGate:
    return DeterministicQualityGate(
        report_id_factory=lambda: QualityReportId("qrep_018bcfe5-6800-7000-8000-000000000098"),
        clock=lambda: UtcTimestamp("2026-09-02T08:00:00Z"),
    )


def test_calibration_reports_rule_confusion_and_units_separately() -> None:
    profile = calibration_profile()
    good = _gate().evaluate(
        ValidationRequest(quality_document(), quality_profile(), profile, "test")
    )
    bad = _gate().evaluate(
        ValidationRequest(
            quality_document("Value 10"),
            quality_profile("Value 20"),
            profile,
            "test",
        )
    )
    report = evaluate_calibration(
        profile,
        (
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="good-page",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=True,
                    failure_type=None,
                    scope_key="page:1",
                ),
                report=good,
            ),
            CalibrationSample(
                truth=CalibrationTruth(
                    sample_id="bad-page",
                    acceptance_unit=AcceptanceUnit.PAGE,
                    meets_acceptance_standard=False,
                    failure_type="NUMERIC.NATIVE_PARSER_DISAGREEMENT",
                    scope_key="page:1",
                ),
                report=bad,
            ),
        ),
    )
    numeric = next(
        metric
        for metric in report.rule_metrics
        if metric.rule_id == "NUMERIC.NATIVE_PARSER_DISAGREEMENT"
        and metric.acceptance_unit is AcceptanceUnit.PAGE
    )
    page = next(
        metric for metric in report.system_metrics if metric.acceptance_unit is AcceptanceUnit.PAGE
    )

    assert numeric.confusion.tp == 1
    assert numeric.confusion.tn == 1
    assert numeric.confusion.precision == 1.0
    assert page.samples == 2
    assert page.coverage == 0.5
    assert page.fallback_rate == 0.5
    assert good.decision is QualityDecision.ACCEPT


def test_profile_cannot_freeze_without_matching_nonempty_evidence() -> None:
    profile = calibration_profile(frozen=False)
    empty = evaluate_calibration(profile, ())

    try:
        freeze_profile(profile, empty)
    except ValueError as exc:
        assert "without calibration samples" in str(exc)
    else:
        raise AssertionError("empty calibration must not freeze automatic routing")

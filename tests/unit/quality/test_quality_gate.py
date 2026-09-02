from __future__ import annotations

from tests.ir_factory import make_block, make_document
from tests.quality_factory import calibration_profile, quality_document, quality_profile

from docparser.ir.enums import ReadingOrderStatus
from docparser.ir.ids import ProvenanceId, QualityReportId
from docparser.ir.types import UtcTimestamp
from docparser.quality import (
    DeterministicQualityGate,
    QualityDecision,
    QualityMode,
    SignalOutcome,
    ValidationRequest,
    apply_quality_report,
)

REPORT_ID = QualityReportId("qrep_018bcfe5-6800-7000-8000-000000000099")


def _gate() -> DeterministicQualityGate:
    return DeterministicQualityGate(
        report_id_factory=lambda: REPORT_ID,
        clock=lambda: UtcTimestamp("2026-09-02T08:00:00Z"),
    )


def test_valid_page_is_accepted_only_with_frozen_profile() -> None:
    document = quality_document()
    profile = quality_profile()

    report = _gate().evaluate(ValidationRequest(document, profile, calibration_profile(), "test"))

    assert report.decision is QualityDecision.ACCEPT
    evaluated = apply_quality_report(document, report)
    assert evaluated.quality_summary.publishable
    assert evaluated.quality_summary.score is None
    assert evaluated.quality_summary.quality_report_id == REPORT_ID


def test_missing_calibration_is_observe_only_and_never_accepts() -> None:
    report = _gate().evaluate(ValidationRequest(quality_document(), quality_profile()))

    assert report.mode is QualityMode.OBSERVE_ONLY
    assert report.decision is QualityDecision.REJECT
    assert report.calibration_required


def test_source_rich_parser_sparse_is_blocking_when_calibrated() -> None:
    report = _gate().evaluate(
        ValidationRequest(
            quality_document("short"),
            quality_profile("This native source contains substantially more content than short"),
            calibration_profile(),
            "test",
        )
    )

    assert report.decision is QualityDecision.FALLBACK_REQUIRED
    assert any(
        signal.rule_id == "COMPLETENESS.SOURCE_RICH_PARSE_SPARSE"
        and signal.outcome is SignalOutcome.TRIGGERED
        for signal in report.signals
    )


def test_numeric_disagreement_preserves_multiplicity() -> None:
    report = _gate().evaluate(
        ValidationRequest(
            quality_document("Value 10"),
            quality_profile("Value 10 and again 10"),
            calibration_profile(),
            "test",
        )
    )
    signal = next(
        signal
        for signal in report.signals
        if signal.rule_id == "NUMERIC.NATIVE_PARSER_DISAGREEMENT"
    )

    assert signal.outcome is SignalOutcome.TRIGGERED
    assert signal.evidence["missing_native_values"] == ["10"]


def test_numeric_rule_is_not_applicable_to_image_only_page() -> None:
    report = _gate().evaluate(
        ValidationRequest(
            quality_document("OCR 10"),
            quality_profile(image_only=True),
            calibration_profile(),
            "test",
        )
    )
    signal = next(
        signal
        for signal in report.signals
        if signal.rule_id == "NUMERIC.NATIVE_PARSER_DISAGREEMENT"
    )

    assert signal.outcome is SignalOutcome.NOT_APPLICABLE


def test_unresolved_order_targets_the_page_without_guessing() -> None:
    unresolved = make_block().model_copy(
        update={
            "reading_order": None,
            "reading_order_status": ReadingOrderStatus.UNRESOLVED,
        }
    )
    document = make_document(blocks=(unresolved,))
    report = _gate().evaluate(
        ValidationRequest(document, quality_profile(), calibration_profile(), "test")
    )
    signal = next(signal for signal in report.signals if signal.rule_id == "ORDER.UNRESOLVED")

    assert signal.outcome is SignalOutcome.TRIGGERED
    assert signal.target.page_number == 1


def test_missing_page_and_broken_provenance_are_hard_rejects() -> None:
    document = quality_document()
    missing_page = document.model_copy(update={"pages": ()})
    missing_report = _gate().evaluate(
        ValidationRequest(missing_page, quality_profile(), calibration_profile(), "test")
    )
    broken_block = (
        document.pages[0]
        .blocks[0]
        .model_copy(
            update={"provenance_ids": (ProvenanceId("prov_643fa564-47f6-566d-9244-cc4076d51abc"),)}
        )
    )
    broken_page = document.pages[0].model_copy(update={"blocks": (broken_block,)})
    broken = document.model_copy(update={"pages": (broken_page,)})
    broken_report = _gate().evaluate(
        ValidationRequest(broken, quality_profile(), calibration_profile(), "test")
    )

    assert missing_report.decision is QualityDecision.REJECT
    assert missing_report.signals[0].rule_id == "INTEGRITY.PAGE_COUNT_MISMATCH"
    assert broken_report.decision is QualityDecision.REJECT
    assert broken_report.signals[0].rule_id == "INTEGRITY.BROKEN_PROVENANCE"

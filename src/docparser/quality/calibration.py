"""Deterministic scope-aware calibration reporting; no threshold optimizer or ML."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable

from docparser.ir.types import Sha256Digest
from docparser.quality.models import (
    AcceptanceUnit,
    CalibrationProfile,
    CalibrationReport,
    CalibrationSample,
    CalibrationTruth,
    ConfusionMetrics,
    FailureLabel,
    QualityDecision,
    QualityMode,
    QualityReport,
    QualityScope,
    QualitySignal,
    RuleAction,
    RuleCalibrationMetrics,
    SignalOutcome,
    SystemCalibrationMetrics,
)

FailureKey = tuple[str, QualityScope, int | None, str | None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _signal_key(signal: QualitySignal) -> FailureKey:
    return (
        str(signal.rule_id),
        signal.target.scope,
        signal.target.page_number,
        str(signal.target.table_id) if signal.target.table_id is not None else None,
    )


def failure_label_matches_signal(label: FailureLabel, signal: QualitySignal) -> bool:
    """Match one predicted failure only to the exact annotated scope."""

    return label.canonical_key == _signal_key(signal)


def _decision_from_signals(signals: Iterable[QualitySignal]) -> QualityDecision:
    blocking = tuple(signal for signal in signals if signal.is_blocking)
    if any(signal.action is RuleAction.REJECT for signal in blocking):
        return QualityDecision.REJECT
    if any(signal.action is RuleAction.FALLBACK for signal in blocking):
        return QualityDecision.FALLBACK_REQUIRED
    return QualityDecision.ACCEPT


def decision_for_scope(
    report: QualityReport,
    acceptance_unit: AcceptanceUnit,
    scope_key: str,
) -> QualityDecision:
    """Derive one acceptance-unit decision without leaking unrelated scope failures."""

    if acceptance_unit is AcceptanceUnit.DOCUMENT:
        return report.decision
    if report.mode is QualityMode.OBSERVE_ONLY:
        return QualityDecision.REJECT

    if acceptance_unit is AcceptanceUnit.PAGE:
        prefix = "page:"
        if not scope_key.startswith(prefix):
            raise ValueError("PAGE scope_key must use page:<number>")
        page_number = int(scope_key.removeprefix(prefix))
        relevant = (
            signal
            for signal in report.signals
            if signal.target.scope is QualityScope.DOCUMENT
            or signal.target.page_number == page_number
            and signal.target.scope in {QualityScope.PAGE, QualityScope.TABLE}
        )
        return _decision_from_signals(relevant)

    prefix = "table:"
    if not scope_key.startswith(prefix):
        raise ValueError("TABLE scope_key must use table:<table_id>")
    table_id = scope_key.removeprefix(prefix)
    relevant = (
        signal
        for signal in report.signals
        if signal.target.scope is QualityScope.DOCUMENT
        or signal.target.scope is QualityScope.TABLE
        and signal.target.table_id is not None
        and str(signal.target.table_id) == table_id
    )
    return _decision_from_signals(relevant)


def _unit_scope_keys(sample: CalibrationSample) -> tuple[str, ...]:
    unit = sample.truth.acceptance_unit
    if unit is AcceptanceUnit.DOCUMENT:
        return ("document",)
    if unit is AcceptanceUnit.PAGE:
        pages = {
            target.page_number
            for target in (signal.target for signal in sample.report.signals)
            if target.scope in {QualityScope.PAGE, QualityScope.TABLE}
            and target.page_number is not None
        }
        pages.update(
            label.page_number
            for label in sample.truth.failure_labels
            if label.scope in {QualityScope.PAGE, QualityScope.TABLE}
            and label.page_number is not None
        )
        return tuple(f"page:{page}" for page in sorted(pages))

    table_ids = {
        str(target.table_id)
        for target in (signal.target for signal in sample.report.signals)
        if target.scope is QualityScope.TABLE and target.table_id is not None
    }
    table_ids.update(
        str(label.table_id)
        for label in sample.truth.failure_labels
        if label.scope is QualityScope.TABLE and label.table_id is not None
    )
    return tuple(f"table:{table_id}" for table_id in sorted(table_ids))


def _scope_meets_standard(truth: CalibrationTruth, scope_key: str) -> bool:
    if truth.meets_acceptance_standard:
        return True
    if not truth.failure_labels:
        return False
    if any(label.scope is QualityScope.DOCUMENT for label in truth.failure_labels):
        return False
    if truth.acceptance_unit is AcceptanceUnit.DOCUMENT:
        return False
    if truth.acceptance_unit is AcceptanceUnit.PAGE:
        page_number = int(scope_key.removeprefix("page:"))
        return not any(
            label.page_number == page_number
            and label.scope in {QualityScope.PAGE, QualityScope.TABLE}
            for label in truth.failure_labels
        )
    table_id = scope_key.removeprefix("table:")
    return not any(
        label.scope is QualityScope.TABLE
        and label.table_id is not None
        and str(label.table_id) == table_id
        for label in truth.failure_labels
    )


def evaluate_calibration(
    profile: CalibrationProfile,
    samples: Iterable[CalibrationSample],
) -> CalibrationReport:
    """Measure exact scoped rule detection and per-unit system decisions."""

    materialized = tuple(samples)
    rule_counts: dict[tuple[str, AcceptanceUnit], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for sample in materialized:
        expected = {label.canonical_key for label in sample.truth.failure_labels}
        observed = {_signal_key(signal) for signal in sample.report.signals}
        triggered = {
            _signal_key(signal)
            for signal in sample.report.signals
            if signal.outcome is SignalOutcome.TRIGGERED
        }
        for key in expected | observed:
            rule_id, scope, _, _ = key
            unit = AcceptanceUnit(scope.value)
            actual = key in expected
            predicted = key in triggered
            counts = rule_counts[(rule_id, unit)]
            if predicted and actual:
                counts[0] += 1
            elif predicted:
                counts[1] += 1
            elif actual:
                counts[3] += 1
            else:
                counts[2] += 1

    rule_metrics = tuple(
        RuleCalibrationMetrics(
            rule_id=rule_id,
            acceptance_unit=unit,
            confusion=ConfusionMetrics(
                tp=counts[0],
                fp=counts[1],
                tn=counts[2],
                fn=counts[3],
                precision=_ratio(counts[0], counts[0] + counts[1]),
                recall=_ratio(counts[0], counts[0] + counts[3]),
                false_positive_rate=_ratio(counts[1], counts[1] + counts[2]),
                false_negative_rate=_ratio(counts[3], counts[3] + counts[0]),
            ),
        )
        for (rule_id, unit), counts in sorted(
            rule_counts.items(), key=lambda item: (item[0][1].value, item[0][0])
        )
    )

    system_metrics: list[SystemCalibrationMetrics] = []
    for unit in AcceptanceUnit:
        decisions: list[tuple[QualityDecision, bool]] = []
        for sample in materialized:
            if sample.truth.acceptance_unit is not unit:
                continue
            for scope_key in _unit_scope_keys(sample):
                decisions.append(
                    (
                        decision_for_scope(sample.report, unit, scope_key),
                        _scope_meets_standard(sample.truth, scope_key),
                    )
                )
        accepted = [
            correct for decision, correct in decisions if decision is QualityDecision.ACCEPT
        ]
        fallback = sum(
            decision is QualityDecision.FALLBACK_REQUIRED for decision, _ in decisions
        )
        unresolved = sum(
            not correct and decision is not QualityDecision.FALLBACK_REQUIRED
            for decision, correct in decisions
        )
        accepted_correct = sum(accepted)
        system_metrics.append(
            SystemCalibrationMetrics(
                acceptance_unit=unit,
                samples=len(decisions),
                accepted=len(accepted),
                accepted_correct=accepted_correct,
                accepted_output_precision=_ratio(accepted_correct, len(accepted)),
                coverage=_ratio(len(accepted), len(decisions)),
                fallback_rate=_ratio(fallback, len(decisions)),
                unresolved_failure_rate=_ratio(unresolved, len(decisions)),
            )
        )
    return CalibrationReport(
        dataset_digest=profile.dataset_digest,
        profile_id=profile.profile_id,
        rule_metrics=rule_metrics,
        system_metrics=tuple(system_metrics),
        sample_count=len(materialized),
    )


def calibration_report_digest(report: CalibrationReport) -> Sha256Digest:
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Sha256Digest(f"sha256:{hashlib.sha256(payload).hexdigest()}")


def freeze_profile(profile: CalibrationProfile, report: CalibrationReport) -> CalibrationProfile:
    """Freeze a matching profile and bind it to its calibration artifact."""

    if report.profile_id != profile.profile_id:
        raise ValueError("calibration report profile_id does not match profile")
    if report.dataset_digest != profile.dataset_digest:
        raise ValueError("calibration report dataset_digest does not match profile")
    if report.sample_count == 0:
        raise ValueError("cannot freeze a profile without calibration samples")
    payload = profile.model_dump(mode="python")
    payload.update(
        {
            "frozen": True,
            "calibration_report_digest": calibration_report_digest(report),
            "calibration_sample_count": report.sample_count,
        }
    )
    return CalibrationProfile.model_validate(payload)

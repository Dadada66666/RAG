"""Deterministic calibration reporting; no threshold optimizer or ML."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from docparser.quality.models import (
    AcceptanceUnit,
    CalibrationProfile,
    CalibrationReport,
    CalibrationSample,
    ConfusionMetrics,
    QualityDecision,
    RuleCalibrationMetrics,
    SignalOutcome,
    SystemCalibrationMetrics,
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_calibration(
    profile: CalibrationProfile,
    samples: Iterable[CalibrationSample],
) -> CalibrationReport:
    """Measure rule detection and system decisions by declared acceptance unit."""

    materialized = tuple(samples)
    rule_counts: dict[tuple[str, AcceptanceUnit], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for sample in materialized:
        triggered = {
            signal.rule_id
            for signal in sample.report.signals
            if signal.outcome is SignalOutcome.TRIGGERED
        }
        rules = {signal.rule_id for signal in sample.report.signals}
        if sample.truth.failure_type is not None:
            rules.add(sample.truth.failure_type)
        for rule_id in rules:
            actual = (
                not sample.truth.meets_acceptance_standard and sample.truth.failure_type == rule_id
            )
            predicted = rule_id in triggered
            counts = rule_counts[(rule_id, sample.truth.acceptance_unit)]
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
        unit_samples = [sample for sample in materialized if sample.truth.acceptance_unit is unit]
        accepted = [
            sample for sample in unit_samples if sample.report.decision is QualityDecision.ACCEPT
        ]
        fallback = [
            sample
            for sample in unit_samples
            if sample.report.decision is QualityDecision.FALLBACK_REQUIRED
        ]
        unresolved = [
            sample
            for sample in unit_samples
            if not sample.truth.meets_acceptance_standard
            and sample.report.decision is not QualityDecision.FALLBACK_REQUIRED
        ]
        accepted_correct = sum(sample.truth.meets_acceptance_standard for sample in accepted)
        system_metrics.append(
            SystemCalibrationMetrics(
                acceptance_unit=unit,
                samples=len(unit_samples),
                accepted=len(accepted),
                accepted_correct=accepted_correct,
                accepted_output_precision=_ratio(accepted_correct, len(accepted)),
                coverage=_ratio(len(accepted), len(unit_samples)),
                fallback_rate=_ratio(len(fallback), len(unit_samples)),
                unresolved_failure_rate=_ratio(len(unresolved), len(unit_samples)),
            )
        )
    return CalibrationReport(
        dataset_digest=profile.dataset_digest,
        profile_id=profile.profile_id,
        rule_metrics=rule_metrics,
        system_metrics=tuple(system_metrics),
        sample_count=len(materialized),
    )


def freeze_profile(profile: CalibrationProfile, report: CalibrationReport) -> CalibrationProfile:
    """Freeze an explicitly selected profile after a non-empty matching report."""

    if report.sample_count == 0:
        raise ValueError("cannot freeze a profile without calibration samples")
    if report.profile_id != profile.profile_id or report.dataset_digest != profile.dataset_digest:
        raise ValueError("calibration report does not match profile")
    payload = profile.model_dump(mode="python")
    payload["frozen"] = True
    return CalibrationProfile.model_validate(payload)

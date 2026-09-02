"""Frozen-evidence fallback planning with a single bounded round."""

from __future__ import annotations

import hashlib
import json

from docparser.fallback.models import FallbackPlan, FallbackProfile, PlannedFallbackTarget
from docparser.ir.models import DocumentIR
from docparser.ir.types import Sha256Digest
from docparser.quality import (
    CalibrationProfile,
    QualityDecision,
    QualityMode,
    QualityReport,
    QualityScope,
    QualityTarget,
)


def _fingerprint(
    document: DocumentIR,
    target: QualityTarget,
    fallback_profile: FallbackProfile,
) -> Sha256Digest:
    payload = {
        "document_id": str(document.document_id),
        "revision_id": str(document.revision_id),
        "target": target.model_dump(mode="json"),
        "parser": fallback_profile.alternate_profile,
        "profile": fallback_profile.profile_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def build_fallback_plan(
    document: DocumentIR,
    report: QualityReport,
    calibration: CalibrationProfile | None,
    fallback_profile: FallbackProfile | None,
) -> FallbackPlan:
    """Plan PAGE/TABLE targets only when both evidence profiles are frozen."""

    if calibration is None or not calibration.frozen:
        return FallbackPlan(
            enabled=False,
            reason="CALIBRATION_REQUIRED",
            targets=(),
            max_rounds=1,
        )
    if fallback_profile is None or not fallback_profile.frozen:
        return FallbackPlan(
            enabled=False,
            reason="FROZEN_FALLBACK_PROFILE_REQUIRED",
            targets=(),
            max_rounds=1,
        )
    if report.mode is not QualityMode.CALIBRATED:
        return FallbackPlan(enabled=False, reason="REPORT_NOT_CALIBRATED", targets=(), max_rounds=1)
    if report.decision is not QualityDecision.FALLBACK_REQUIRED:
        return FallbackPlan(enabled=False, reason="FALLBACK_NOT_REQUIRED", targets=(), max_rounds=1)

    grouped: dict[tuple[str, int | None, str | None], list[str]] = {}
    targets = {}
    for signal in report.blocking_signals:
        if signal.rule_id not in fallback_profile.eligible_rule_ids:
            continue
        if signal.target.scope not in {QualityScope.PAGE, QualityScope.TABLE}:
            continue
        key = (
            signal.target.scope.value,
            signal.target.page_number,
            str(signal.target.table_id) if signal.target.table_id else None,
        )
        grouped.setdefault(key, []).append(signal.rule_id)
        targets[key] = signal.target

    page_targets = {
        target.page_number for target in targets.values() if target.scope is QualityScope.PAGE
    }
    for key, target in tuple(targets.items()):
        if target.scope is QualityScope.TABLE and target.page_number in page_targets:
            del targets[key]
            del grouped[key]

    ordered_keys = sorted(grouped, key=lambda item: (item[1] or 0, item[0], item[2] or ""))
    ordered_keys = ordered_keys[: fallback_profile.budget.max_targets]
    allowed_pages = max(1, int(document.page_count * fallback_profile.budget.max_page_fraction))
    selected: list[PlannedFallbackTarget] = []
    selected_pages: set[int] = set()
    for key in ordered_keys:
        target = targets[key]
        assert target.page_number is not None
        if target.page_number not in selected_pages and len(selected_pages) >= allowed_pages:
            continue
        selected_pages.add(target.page_number)
        selected.append(
            PlannedFallbackTarget(
                target=target,
                triggering_rule_ids=tuple(sorted(set(grouped[key]))),
                attempt_fingerprint=_fingerprint(document, target, fallback_profile),
            )
        )
    return FallbackPlan(
        enabled=bool(selected),
        reason="FROZEN_EVIDENCE_ROUTING" if selected else "NO_ELIGIBLE_TARGET",
        targets=tuple(selected),
        max_rounds=1,
    )

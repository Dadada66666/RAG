"""Synchronous parse -> quality -> selective fallback -> revalidation workflow."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.fallback import (
    CandidatePage,
    FallbackProfile,
    FallbackResult,
    FallbackTargetResult,
    FallbackTargetStatus,
    RobustDiagnostics,
    RobustParseOutcome,
    UnsupportedDependencyError,
    build_fallback_plan,
    match_table_candidate,
    materialize_page,
    replace_page_atomic,
    replace_table_atomic,
)
from docparser.ir.base import StrictIRModel
from docparser.ir.serialization import dump_canonical_json
from docparser.ports.parsers import DocumentParser
from docparser.quality import (
    CalibrationProfile,
    DeterministicQualityGate,
    QualityReport,
    QualityScope,
    QualityTarget,
    ValidationRequest,
    apply_quality_report,
)

ParseProvider = Callable[[Path, ParsingConfig, DocumentParser | None], ParseOutcome]


def _parse_provider(
    path: Path,
    config: ParsingConfig,
    parser: DocumentParser | None,
) -> ParseOutcome:
    return parse_document_with_diagnostics(path, config, parser=parser)


def _blocking_keys(report: QualityReport) -> set[tuple[str, str, int | None, str | None]]:
    return {
        (
            signal.rule_id,
            signal.target.scope.value,
            signal.target.page_number,
            str(signal.target.table_id) if signal.target.table_id else None,
        )
        for signal in report.blocking_signals
    }


def _target_trigger_keys(
    report: QualityReport,
    target: QualityTarget,
    triggering_rule_ids: tuple[str, ...],
) -> set[tuple[str, str, int | None, str | None]]:
    return {
        key
        for key in _blocking_keys(report)
        if key[0] in triggering_rule_ids
        and key[1] == target.scope.value
        and key[2] == target.page_number
        and key[3] == (str(target.table_id) if target.table_id is not None else None)
    }


def _clear_gain(
    before: QualityReport,
    after: QualityReport,
    target: QualityTarget,
    triggering_rule_ids: tuple[str, ...],
) -> bool:
    before_keys = _blocking_keys(before)
    trigger_keys = _target_trigger_keys(before, target, triggering_rule_ids)
    after_keys = _blocking_keys(after)
    return (
        bool(trigger_keys)
        and not (after_keys & trigger_keys)
        and not (after_keys - (before_keys - trigger_keys))
    )


def robust_parse_document(
    path: Path,
    primary_config: ParsingConfig,
    *,
    calibration: CalibrationProfile | None = None,
    fallback_profile: FallbackProfile | None = None,
    supported_slice: str | None = None,
    primary_parser: DocumentParser | None = None,
    alternate_parser: DocumentParser | None = None,
    quality_gate: DeterministicQualityGate | None = None,
    parse_provider: ParseProvider = _parse_provider,
) -> RobustParseOutcome:
    """Return a trusted evaluated IR or an honestly non-publishable baseline."""

    gate = quality_gate or DeterministicQualityGate()
    if calibration is not None and calibration.parser_profile != primary_config.parser:
        raise ValueError("calibration parser_profile does not match primary parser configuration")
    if (
        fallback_profile is not None
        and fallback_profile.primary_profile != primary_config.parser
    ):
        raise ValueError("fallback profile primary_profile does not match parser configuration")
    if (
        calibration is not None
        and fallback_profile is not None
        and fallback_profile.evidence_dataset_digest != calibration.dataset_digest
    ):
        raise ValueError("quality and fallback profiles reference different evidence datasets")
    baseline_outcome = parse_provider(path, primary_config, primary_parser)
    baseline_report = gate.evaluate(
        ValidationRequest(
            document=baseline_outcome.document,
            profile=baseline_outcome.profile,
            calibration=calibration,
            supported_slice=supported_slice,
        )
    )
    baseline = apply_quality_report(baseline_outcome.document, baseline_report)
    plan = build_fallback_plan(
        baseline,
        baseline_report,
        calibration,
        fallback_profile,
        supported_slice,
    )
    working = baseline
    current_report = baseline_report
    results: list[FallbackTargetResult] = []
    attempted_fingerprints: set[str] = set()

    if plan.enabled and fallback_profile is not None:
        alternate_config = ParsingConfig(
            parser=fallback_profile.alternate_profile,
            device=primary_config.device,
            tenant_scope=primary_config.tenant_scope,
            namespace=primary_config.namespace,
        )
        with TemporaryDirectory(prefix="docparser-fallback-") as temporary:
            for index, planned in enumerate(plan.targets):
                fingerprint = str(planned.attempt_fingerprint)
                if fingerprint in attempted_fingerprints:
                    continue
                attempted_fingerprints.add(fingerprint)
                assert planned.target.page_number is not None
                materialized = materialize_page(
                    path,
                    working.document_id,
                    planned.target.page_number,
                    Path(temporary) / f"page-{planned.target.page_number}-{index}.pdf",
                )
                try:
                    candidate_outcome = parse_provider(
                        materialized.temporary_pdf,
                        alternate_config,
                        alternate_parser,
                    )
                    candidate = CandidatePage(
                        original_page_number=planned.target.page_number,
                        materialized_digest=materialized.digest,
                        document=candidate_outcome.document,
                    )
                    if planned.target.scope is QualityScope.PAGE:
                        proposed = replace_page_atomic(
                            working,
                            candidate,
                            attempt_fingerprint=fingerprint,
                            triggering_rule_ids=planned.triggering_rule_ids,
                        )
                    else:
                        assert planned.target.table_id is not None
                        baseline_table = next(
                            table
                            for table in working.tables
                            if table.table_id == planned.target.table_id
                        )
                        if len(baseline_table.segments) > 1:
                            results.append(
                                FallbackTargetResult(
                                    target=planned.target,
                                    status=FallbackTargetStatus.UNSUPPORTED_CROSS_PAGE_TABLE_FALLBACK,
                                    attempt_fingerprint=planned.attempt_fingerprint,
                                    alternate_profile=fallback_profile.alternate_profile,
                                    materialized_digest=materialized.digest,
                                    detail="Cross-page table fallback is outside the MVP.",
                                )
                            )
                            continue
                        match = match_table_candidate(
                            baseline_table,
                            candidate.document.tables,
                            minimum_score=fallback_profile.minimum_candidate_match,
                            winner_margin=fallback_profile.winner_margin,
                        )
                        if match.status == "CONFLICT":
                            results.append(
                                FallbackTargetResult(
                                    target=planned.target,
                                    status=FallbackTargetStatus.CONFLICT,
                                    attempt_fingerprint=planned.attempt_fingerprint,
                                    alternate_profile=fallback_profile.alternate_profile,
                                    materialized_digest=materialized.digest,
                                    detail="Two candidate tables are too close to select safely.",
                                )
                            )
                            continue
                        if match.candidate is None:
                            results.append(
                                FallbackTargetResult(
                                    target=planned.target,
                                    status=FallbackTargetStatus.REJECTED_NO_CLEAR_GAIN,
                                    attempt_fingerprint=planned.attempt_fingerprint,
                                    alternate_profile=fallback_profile.alternate_profile,
                                    materialized_digest=materialized.digest,
                                    detail=(
                                        "No candidate table met the frozen compatibility predicate."
                                    ),
                                )
                            )
                            continue
                        proposed = replace_table_atomic(
                            working,
                            candidate,
                            baseline_table,
                            match.candidate,
                            attempt_fingerprint=fingerprint,
                            triggering_rule_ids=planned.triggering_rule_ids,
                        )
                    proposed_report = gate.evaluate(
                        ValidationRequest(
                            document=proposed,
                            profile=baseline_outcome.profile,
                            calibration=calibration,
                            supported_slice=supported_slice,
                        )
                    )
                    if not _clear_gain(
                        current_report,
                        proposed_report,
                        planned.target,
                        planned.triggering_rule_ids,
                    ):
                        results.append(
                            FallbackTargetResult(
                                target=planned.target,
                                status=FallbackTargetStatus.REJECTED_NO_CLEAR_GAIN,
                                attempt_fingerprint=planned.attempt_fingerprint,
                                alternate_profile=fallback_profile.alternate_profile,
                                materialized_digest=materialized.digest,
                                detail=(
                                    "Candidate did not resolve its trigger without new blockers."
                                ),
                            )
                        )
                        continue
                    working = apply_quality_report(proposed, proposed_report)
                    current_report = proposed_report
                    results.append(
                        FallbackTargetResult(
                            target=planned.target,
                            status=FallbackTargetStatus.APPLIED,
                            attempt_fingerprint=planned.attempt_fingerprint,
                            alternate_profile=fallback_profile.alternate_profile,
                            materialized_digest=materialized.digest,
                            detail=(
                                "Candidate resolved the frozen predicate and passed full "
                                "revalidation."
                            ),
                        )
                    )
                except UnsupportedDependencyError as exc:
                    results.append(
                        FallbackTargetResult(
                            target=planned.target,
                            status=FallbackTargetStatus.REJECTED_UNSUPPORTED_DEPENDENCY,
                            attempt_fingerprint=planned.attempt_fingerprint,
                            alternate_profile=fallback_profile.alternate_profile,
                            materialized_digest=materialized.digest,
                            detail=str(exc),
                        )
                    )
                except (OSError, RuntimeError, ValueError, StopIteration) as exc:
                    results.append(
                        FallbackTargetResult(
                            target=planned.target,
                            status=FallbackTargetStatus.PARSER_FAILED,
                            attempt_fingerprint=planned.attempt_fingerprint,
                            alternate_profile=fallback_profile.alternate_profile,
                            materialized_digest=materialized.digest,
                            detail=str(exc) or type(exc).__name__,
                        )
                    )

    fallback_result = (
        FallbackResult(
            attempted=len(results),
            applied=sum(result.status is FallbackTargetStatus.APPLIED for result in results),
            results=tuple(results),
        )
        if plan.enabled
        else None
    )
    diagnostics = RobustDiagnostics(
        mode=current_report.mode.value,
        calibration_profile=calibration.profile_id if calibration else None,
        fallback_profile=fallback_profile.profile_id if fallback_profile else None,
        fallback_attempts=len(results),
        fallback_applied=sum(result.status is FallbackTargetStatus.APPLIED for result in results),
        baseline_parser_diagnostics=baseline_outcome.diagnostics,
    )
    return RobustParseOutcome(
        baseline_document=baseline,
        baseline_quality_report=baseline_report,
        fallback_plan=plan,
        fallback_result=fallback_result,
        final_document=working,
        final_quality_report=current_report,
        final_decision=current_report.decision,
        diagnostics=diagnostics,
    )


def _write_model(path: Path, model: StrictIRModel) -> None:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    path.write_text(payload, encoding="utf-8")


def write_robust_outputs(outcome: RobustParseOutcome, output: Path) -> None:
    """Write bounded robust-parse artifacts without temporary host paths."""

    output.mkdir(parents=True, exist_ok=True)
    (output / "baseline.ir.json").write_bytes(dump_canonical_json(outcome.baseline_document))
    _write_model(output / "baseline-quality.json", outcome.baseline_quality_report)
    if outcome.fallback_plan is not None:
        _write_model(output / "fallback-plan.json", outcome.fallback_plan)
    if outcome.fallback_result is not None:
        _write_model(output / "fallback-result.json", outcome.fallback_result)
    (output / "final.ir.json").write_bytes(dump_canonical_json(outcome.final_document))
    _write_model(output / "final-quality.json", outcome.final_quality_report)
    _write_model(output / "robust-diagnostics.json", outcome.diagnostics)

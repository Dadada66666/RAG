from __future__ import annotations

import os
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from tests.pdf_factory import write_tiny_pdf
from tests.quality_factory import calibration_profile

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.application.robust import robust_parse_document, write_robust_outputs
from docparser.domain.parser_contract import RuntimeDevice
from docparser.fallback import FallbackBudget, FallbackProfile, FallbackTargetStatus
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser
from docparser.quality import (
    CalibrationProfile,
    DeterministicQualityGate,
    QualityScope,
    QualitySignal,
    QualityTarget,
    RuleAction,
    SignalOutcome,
    ValidationRequest,
)
from docparser.quality.models import SignalKind, SignalSeverity

REAL_SLICE = "real-robust-smoke"
TEST_RULE = "ORDER.UNRESOLVED"
TABLE_RULE = "TABLE.DEGENERATE_STRUCTURE"


class _BaselineOnlyBlockingRule:
    def __init__(self, scope: QualityScope) -> None:
        self.scope = scope
        self.rule_id = TABLE_RULE if scope is QualityScope.TABLE else TEST_RULE

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]:
        baseline_parser_present = any(
            run.parser_name == "docling" for run in context.document.processing.parser_runs
        )
        page_number = 2 if context.document.page_count > 1 else 1
        table = (
            next(
                table
                for table in context.document.tables
                if table.segments[0].page_number == page_number
            )
            if self.scope is QualityScope.TABLE
            else None
        )
        target = QualityTarget(
            scope=self.scope,
            page_number=page_number,
            table_id=table.table_id if table is not None else None,
        )
        return (
            QualitySignal(
                rule_id=self.rule_id,
                signal_kind=SignalKind.UNCERTAINTY,
                severity=SignalSeverity.ERROR,
                outcome=(
                    SignalOutcome.TRIGGERED
                    if baseline_parser_present
                    else SignalOutcome.CLEAR
                ),
                target=target,
                predicted_failure_type="REAL_FALLBACK_ORCHESTRATION_SMOKE",
                action=RuleAction.FALLBACK,
                calibrated=True,
                evidence={"baseline_parser_present": baseline_parser_present},
                message="Test-only trigger for real fallback orchestration.",
            ),
        )


def _profiles(rule_id: str) -> tuple[CalibrationProfile, FallbackProfile]:
    calibration = calibration_profile().model_copy(
        update={"supported_slices": (REAL_SLICE,), "rule_actions": {rule_id: RuleAction.FALLBACK}}
    )
    fallback = FallbackProfile(
        profile_id="real-robust-smoke-v1",
        evidence_dataset_digest=calibration.dataset_digest,
        evidence_report_digest=Sha256Digest(f"sha256:{'e' * 64}"),
        evidence_sample_count=20,
        created_from_commit="integration-smoke",
        primary_profile="docling-standard",
        alternate_profile="paddleocr-vl-1.6",
        supported_slice=REAL_SLICE,
        eligible_rule_ids=(rule_id,),
        minimum_candidate_match=0.45,
        winner_margin=0.05,
        budget=FallbackBudget(max_rounds=1, max_targets=1, max_page_fraction=1.0),
        frozen=True,
    )
    return calibration, fallback


def _two_page_source(tmp_path: Path, layout: str) -> Path:
    first = write_tiny_pdf(tmp_path / "first.pdf")
    second = write_tiny_pdf(tmp_path / f"second-{layout}.pdf", layout=layout)
    source = tmp_path / f"robust-{layout}.pdf"
    writer = PdfWriter()
    writer.add_page(PdfReader(first, strict=True).pages[0])
    writer.add_page(PdfReader(second, strict=True).pages[0])
    with source.open("wb") as stream:
        writer.write(stream)
    return source


@pytest.mark.integration
@pytest.mark.parser
@pytest.mark.gpu
@pytest.mark.parametrize(
    ("layout", "scope", "rule_id"),
    (
        ("single", QualityScope.PAGE, TEST_RULE),
        ("table", QualityScope.TABLE, TABLE_RULE),
    ),
)
def test_real_docling_to_paddle_enters_single_page_fallback_and_rejects_without_clear_gain(
    tmp_path: Path,
    layout: str,
    scope: QualityScope,
    rule_id: str,
) -> None:
    if os.environ.get("DOCPARSER_RUN_ROBUST_SMOKE") != "1":
        pytest.skip(
            "set DOCPARSER_RUN_ROBUST_SMOKE=1 with pinned Docling/Paddle GPU runtimes installed"
        )
    source = _two_page_source(tmp_path, layout)
    calibration, fallback = _profiles(rule_id)
    gate = DeterministicQualityGate(rules=(_BaselineOnlyBlockingRule(scope),))
    parsed_page_counts: list[int] = []

    def parse_provider(
        path: Path,
        config: ParsingConfig,
        parser: DocumentParser | None,
    ) -> ParseOutcome:
        parsed_page_counts.append(len(PdfReader(path, strict=True).pages))
        return parse_document_with_diagnostics(path, config, parser=parser)

    outcome = robust_parse_document(
        source,
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CUDA),
        calibration=calibration,
        fallback_profile=fallback,
        supported_slice=REAL_SLICE,
        quality_gate=gate,
        parse_provider=parse_provider,
    )

    assert parsed_page_counts == [2, 1]
    assert outcome.fallback_result is not None
    result = outcome.fallback_result.results[0]
    assert result.status is FallbackTargetStatus.REJECTED_NO_CLEAR_GAIN
    assert result.materialized_digest is not None
    assert outcome.final_document.revision_id == outcome.baseline_document.revision_id
    output = tmp_path / f"robust-{layout}-output"
    write_robust_outputs(outcome, output)
    assert str(result.materialized_digest) in (output / "fallback-result.json").read_text(
        encoding="utf-8"
    )
    if scope is QualityScope.TABLE:
        assert outcome.baseline_document.tables
        assert outcome.final_document.tables == outcome.baseline_document.tables

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from tests.parser_fixture import load_contract_result
from tests.pdf_factory import write_tiny_pdf
from tests.quality_factory import calibration_profile
from tests.unit.application.test_parsing import ContractFixtureParser

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.application.robust import robust_parse_document
from docparser.domain.parser_contract import ParseResult, RuntimeDevice
from docparser.fallback import FallbackBudget, FallbackProfile, FallbackTargetStatus
from docparser.ir.ids import ParserRunId
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser
from docparser.quality import QualityDecision, QualityMode


def _result_with_paragraph(
    text: str,
    *,
    parser_name: str,
    parser_run_id: str,
) -> ParseResult:
    result = load_contract_result("born-digital")
    page = result.pages[0]
    elements = tuple(
        element.model_copy(update={"text": text})
        if element.element_type.value == "PARAGRAPH"
        else element
        for element in page.elements
    )
    return result.model_copy(
        update={
            "descriptor": result.descriptor.model_copy(
                update={"parser_name": parser_name, "profile": parser_name}
            ),
            "run": result.run.model_copy(update={"parser_run_id": ParserRunId(parser_run_id)}),
            "pages": (page.model_copy(update={"elements": elements}),),
        }
    )


def test_robust_parse_materializes_one_page_and_commits_clear_gain(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    primary = ContractFixtureParser(
        _result_with_paragraph(
            "Revenue missing",
            parser_name="docling",
            parser_run_id="prun_018bcfe5-6800-7000-8000-000000000011",
        )
    )
    alternate = ContractFixtureParser(
        _result_with_paragraph(
            "Revenue 184,392.17 USD",
            parser_name="paddleocr-vl",
            parser_run_id="prun_018bcfe5-6800-7000-8000-000000000012",
        )
    )
    fallback_profile = FallbackProfile(
        profile_id="fallback-test-v1",
        evidence_dataset_digest=Sha256Digest(f"sha256:{'c' * 64}"),
        evidence_report_digest=Sha256Digest(f"sha256:{'e' * 64}"),
        evidence_sample_count=20,
        created_from_commit="test-commit",
        primary_profile="docling-standard",
        alternate_profile="paddleocr-vl-1.6",
        supported_slice="test",
        eligible_rule_ids=("NUMERIC.NATIVE_PARSER_DISAGREEMENT",),
        minimum_candidate_match=0.5,
        winner_margin=0.1,
        budget=FallbackBudget(max_rounds=1, max_targets=1, max_page_fraction=1.0),
        frozen=True,
    )
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
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
        calibration=calibration_profile(),
        fallback_profile=fallback_profile,
        supported_slice="test",
        primary_parser=primary,
        alternate_parser=alternate,
        parse_provider=parse_provider,
    )

    assert parsed_page_counts == [1, 1]
    assert outcome.baseline_quality_report.decision is QualityDecision.FALLBACK_REQUIRED
    assert outcome.final_decision is QualityDecision.ACCEPT
    assert outcome.fallback_result is not None
    assert outcome.fallback_result.results[0].status is FallbackTargetStatus.APPLIED
    assert outcome.final_document.revision_number == 1
    assert outcome.final_document.previous_revision_id == outcome.baseline_document.revision_id
    assert outcome.final_document.quality_summary.score is None
    materialized_digest = outcome.fallback_result.results[0].materialized_digest
    assert materialized_digest is not None
    assert outcome.final_document.processing.parser_runs[-1].runtime["materialized_digest"] == str(
        materialized_digest
    )

    bad_alternate = ContractFixtureParser(
        _result_with_paragraph(
            "Still missing",
            parser_name="paddleocr-vl",
            parser_run_id="prun_018bcfe5-6800-7000-8000-000000000013",
        )
    )
    rejected = robust_parse_document(
        source,
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
        calibration=calibration_profile(),
        fallback_profile=fallback_profile,
        supported_slice="test",
        primary_parser=primary,
        alternate_parser=bad_alternate,
        parse_provider=parse_provider,
    )

    assert rejected.fallback_result is not None
    assert (
        rejected.fallback_result.results[0].status
        is FallbackTargetStatus.REJECTED_NO_CLEAR_GAIN
    )
    assert rejected.final_document.revision_id == rejected.baseline_document.revision_id


def test_robust_parse_without_calibration_never_runs_fallback(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    primary = ContractFixtureParser(load_contract_result("born-digital"))

    outcome = robust_parse_document(
        source,
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
        primary_parser=primary,
    )

    assert outcome.final_decision is QualityDecision.REJECT
    assert outcome.final_quality_report.calibration_required
    assert outcome.fallback_plan is not None
    assert not outcome.fallback_plan.enabled
    assert outcome.fallback_result is None


def test_calibrated_accept_does_not_require_fallback_profile(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "accepted.pdf")
    primary = ContractFixtureParser(load_contract_result("born-digital"))

    outcome = robust_parse_document(
        source,
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
        calibration=calibration_profile(),
        supported_slice="test",
        primary_parser=primary,
    )

    assert outcome.final_decision is QualityDecision.ACCEPT
    assert outcome.final_document.quality_summary.publishable
    assert outcome.fallback_result is None


def test_candidate_calibration_profile_never_executes_automatic_fallback(
    tmp_path: Path,
) -> None:
    source = write_tiny_pdf(tmp_path / "candidate-calibration.pdf", layout="numeric")
    primary = ContractFixtureParser(
        _result_with_paragraph(
            "Revenue missing",
            parser_name="docling",
            parser_run_id="prun_018bcfe5-6800-7000-8000-000000000021",
        )
    )
    fallback_profile = FallbackProfile(
        profile_id="fallback-test-v1",
        evidence_dataset_digest=Sha256Digest(f"sha256:{'c' * 64}"),
        evidence_report_digest=Sha256Digest(f"sha256:{'e' * 64}"),
        evidence_sample_count=20,
        created_from_commit="test-commit",
        primary_profile="docling-standard",
        alternate_profile="paddleocr-vl-1.6",
        supported_slice="test",
        eligible_rule_ids=("NUMERIC.NATIVE_PARSER_DISAGREEMENT",),
        minimum_candidate_match=0.5,
        winner_margin=0.1,
        frozen=True,
    )
    parse_calls = 0

    def parse_provider(
        path: Path,
        config: ParsingConfig,
        parser: DocumentParser | None,
    ) -> ParseOutcome:
        nonlocal parse_calls
        parse_calls += 1
        return parse_document_with_diagnostics(path, config, parser=parser)

    outcome = robust_parse_document(
        source,
        ParsingConfig(parser="docling-standard", device=RuntimeDevice.CPU),
        calibration=calibration_profile(frozen=False),
        fallback_profile=fallback_profile,
        supported_slice="test",
        primary_parser=primary,
        parse_provider=parse_provider,
    )

    assert outcome.baseline_quality_report.mode is QualityMode.CALIBRATION
    assert outcome.baseline_quality_report.decision is QualityDecision.FALLBACK_REQUIRED
    assert outcome.fallback_plan is not None
    assert not outcome.fallback_plan.enabled
    assert outcome.fallback_result is None
    assert parse_calls == 1

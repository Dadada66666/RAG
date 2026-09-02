from __future__ import annotations

import hashlib
from pathlib import Path

from tests.parser_fixture import load_contract_result
from tests.pdf_factory import write_tiny_pdf
from tests.unit.application.test_parsing import ContractFixtureParser
from tests.unit.normalization.test_paddleocr_vl_normalizer import (
    _result,
    _StaticPaddleParser,
)

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.evaluation.benchmark import (
    run_parsing_benchmark,
    summarize_cases,
    write_benchmark_report,
)
from docparser.evaluation.models import (
    BenchmarkCaseResult,
    BenchmarkOutputStatus,
    CriticalNumericTruth,
    DatasetSlice,
    EvaluationDenominators,
    GoldenDatasetManifest,
    GoldenDocument,
    MetricStatus,
    MetricValues,
    PageAnnotation,
    TextTruth,
)
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser


def _manifest_for(source: Path) -> GoldenDatasetManifest:
    digest = Sha256Digest(f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}")
    return GoldenDatasetManifest(
        dataset_id="unit-development",
        version="0.1.0",
        target_page_count_min=1,
        target_page_count_max=5,
        documents=(
            GoldenDocument(
                document_id="numeric-page",
                local_path=Path(source.name),
                source_sha256=digest,
                slices=(DatasetSlice.BORN_DIGITAL, DatasetSlice.FINANCIAL_TABLE),
                annotations=(
                    PageAnnotation(
                        page_number=1,
                        text=TextTruth(expected_text="Revenue 184,392.17 USD"),
                        critical_numerics=(
                            CriticalNumericTruth(
                                truth_id="revenue-total",
                                value="184,392.17",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_runner_compares_same_manifest_without_global_score(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    manifest = _manifest_for(source)

    def parse(path: Path, config: ParsingConfig) -> ParseOutcome:
        parser: DocumentParser
        if config.parser == "paddleocr-vl-1.6":
            parser = _StaticPaddleParser(_result())
        else:
            parser = ContractFixtureParser(load_contract_result("born-digital"))
        return parse_document_with_diagnostics(path, config, parser=parser)

    report = run_parsing_benchmark(
        manifest,
        manifest_dir=tmp_path,
        parse_fn=parse,
    )
    output = tmp_path / "report"
    write_benchmark_report(report, output)

    assert {result.parser_profile for result in report.results} == {
        "docling-standard",
        "paddleocr-vl-1.6",
    }
    assert report.recommendation == "profile-based routing pending slice review"
    assert {comparison.slice for comparison in report.slice_comparisons} == {
        DatasetSlice.BORN_DIGITAL,
        DatasetSlice.FINANCIAL_TABLE,
    }
    assert all(len(comparison.parsers) == 2 for comparison in report.slice_comparisons)
    assert all(result.execution is not None for result in report.results)
    assert {result.execution.parser_version for result in report.results if result.execution} == {
        "2.123.0",
        "3.7.0",
    }
    assert report.metric_implementation_version == "project-parsing-metrics@2.1.0"
    assert report.text_assembly_profile == "canonical-reading-flow-with-logical-tables@1.0.0"
    assert (output / "parsing-benchmark.json").is_file()
    markdown = (output / "parsing-benchmark.md").read_text(encoding="utf-8")
    assert "No global parser score" in markdown
    assert "Official ParseBench metrics or scores" in markdown


def test_parser_failure_remains_in_cases_and_slice_denominators(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    manifest = _manifest_for(source)

    def parse(path: Path, config: ParsingConfig) -> ParseOutcome:
        if config.parser == "paddleocr-vl-1.6":
            raise RuntimeError("intentional parser failure")
        return parse_document_with_diagnostics(
            path,
            config,
            parser=ContractFixtureParser(load_contract_result("born-digital")),
        )

    report = run_parsing_benchmark(manifest, manifest_dir=tmp_path, parse_fn=parse)
    paddle = next(
        result for result in report.results if result.parser_profile == "paddleocr-vl-1.6"
    )
    paddle_summary = next(
        parser
        for comparison in report.slice_comparisons
        if comparison.slice is DatasetSlice.BORN_DIGITAL
        for parser in comparison.parsers
        if parser.parser_profile == "paddleocr-vl-1.6"
    )

    assert len(report.results) == 2
    assert paddle.output_status is BenchmarkOutputStatus.PARSER_FAILED
    assert paddle.denominators.pages == 1
    assert paddle_summary.failed_outputs == 1
    assert paddle_summary.output_coverage == 0.0
    assert paddle_summary.pages == 1


def _metric(*, pages_expected: int, pages_present: int, cells_correct: int) -> MetricValues:
    return MetricValues.model_construct(
        pages_expected=pages_expected,
        pages_present=pages_present,
        page_completeness=pages_present / pages_expected,
        text_metric_status=MetricStatus.NOT_APPLICABLE,
        text_edit_similarity=None,
        text_pages_scored=0,
        reading_order_pairs_correct=0,
        table_detection_tp=0,
        table_detection_fp=0,
        cells_text_correct=cells_correct,
        page_numeric_presence_correct=0,
        structural_numerics_correct=0,
        resolvable_block_provenance=1.0,
        elapsed_seconds=1.0,
    )


def _case(
    *, document_id: str, pages: int, pages_present: int, cells: int, correct: int
) -> BenchmarkCaseResult:
    return BenchmarkCaseResult(
        document_id=document_id,
        parser_profile="docling-standard",
        slices=(DatasetSlice.BORN_DIGITAL,),
        output_status=BenchmarkOutputStatus.SUCCESS,
        denominators=EvaluationDenominators(
            pages=pages,
            text_pages=0,
            reading_order_pairs=0,
            tables=1,
            cells=cells,
            numeric_annotations=0,
            structural_numeric_annotations=0,
        ),
        metrics=_metric(
            pages_expected=pages,
            pages_present=pages_present,
            cells_correct=correct,
        ),
    )


def test_document_macro_and_page_cell_micro_keep_their_denominators() -> None:
    small = _case(document_id="small", pages=1, pages_present=1, cells=1, correct=1)
    large = _case(document_id="large", pages=9, pages_present=0, cells=9, correct=0)

    summary = summarize_cases((small, large), "docling-standard")

    assert summary.page_completeness_document_macro == 0.5
    assert summary.page_completeness_page_macro == 0.1
    assert summary.cell_exact_text_accuracy_micro == 0.1

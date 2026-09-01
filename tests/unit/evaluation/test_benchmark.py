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
from docparser.evaluation.benchmark import run_parsing_benchmark, write_benchmark_report
from docparser.evaluation.models import (
    CriticalNumericTruth,
    DatasetSlice,
    GoldenDatasetManifest,
    GoldenDocument,
    PageAnnotation,
    TextTruth,
)
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser


def test_runner_compares_same_manifest_without_global_score(tmp_path: Path) -> None:
    source = write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    digest = Sha256Digest(f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}")
    manifest = GoldenDatasetManifest(
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
                        critical_numerics=(CriticalNumericTruth(value="184,392.17"),),
                    ),
                ),
            ),
        ),
    )

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
    assert (output / "parsing-benchmark.json").is_file()
    markdown = (output / "parsing-benchmark.md").read_text(encoding="utf-8")
    assert "No global parser score" in markdown

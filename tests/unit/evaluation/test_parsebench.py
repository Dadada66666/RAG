from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tests.parser_fixture import (
    load_contract_result,
    normalize_contract_fixture,
    profile_for_result,
)
from tests.pdf_factory import write_tiny_pdf
from tests.unit.application.test_parsing import ContractFixtureParser

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.evaluation.parsebench.export import export_document_to_parsebench
from docparser.evaluation.parsebench.models import (
    PARSEBENCH_COMMIT,
    ParseBenchCandidate,
    ParseBenchRunRequest,
    ParseBenchStratum,
    ParseBenchSubsetManifest,
    SubsetSelectionStatus,
)
from docparser.evaluation.parsebench.runner import (
    official_evaluator_command,
    run_official_parsebench,
)
from docparser.evaluation.parsebench.subset import prepare_subset_manifests
from docparser.evaluation.parsebench.workflow import prepare_parsebench_predictions
from docparser.ir.types import Sha256Digest


def test_canonical_export_preserves_merged_table_structure() -> None:
    document = normalize_contract_fixture("merged-table")

    exported = export_document_to_parsebench(
        document,
        example_id="merged-table-example",
        pipeline_name="docling-standard",
        source_file_path="dataset/merged-table.pdf",
        latency_in_ms=125,
    )

    assert exported.output.example_id == "merged-table-example"
    assert 'colspan="2"' in exported.output.markdown
    assert exported.output.layout_pages[0].items[0].type == "table"
    assert exported.raw_output["adapter_version"] == "parsebench-export@1.0.0"


def test_official_runner_only_wraps_pinned_external_evaluator_output(
    tmp_path: Path,
) -> None:
    report_root = tmp_path / "report"
    result_path = report_root / "_evaluation_report.json"
    calls: list[tuple[str, ...]] = []

    def execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        del cwd
        call = tuple(command)
        calls.append(call)
        if call == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(call, 0, stdout=f"{PARSEBENCH_COMMIT}\n", stderr="")
        report_root.mkdir()
        result_path.write_text(
            json.dumps(
                {
                    "total_examples": 80,
                    "successful": 80,
                    "per_example_results": [
                        {"test_id": f"table/example-{index}", "metric": "x" * 256}
                        for index in range(80)
                    ],
                    "aggregate_metrics": {"table_gtrm": 0.75},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(call, 0, stdout="ok", stderr="")

    request = ParseBenchRunRequest(
        benchmark_id="official-parsebench-smoke-v1",
        subset_id="parsebench-complex-v1-dev",
        subset_manifest_digest=Sha256Digest(f"sha256:{'b' * 64}"),
        checkout_path=tmp_path,
        parsebench_python=tmp_path / "parsebench-python",
        dataset_root=tmp_path / "dataset",
        export_root=tmp_path / "predictions",
        report_root=report_root,
        environment_digest=Sha256Digest(f"sha256:{'a' * 64}"),
        hardware_description="unit-test-cpu",
    )
    result = run_official_parsebench(request, executor=execute)

    assert calls == [
        ("git", "rev-parse", "HEAD"),
        official_evaluator_command(request),
    ]
    assert result.terminology == "OFFICIAL_PARSEBENCH_METRIC"
    assert result.repository_commit == PARSEBENCH_COMMIT
    assert result.subset_id == "parsebench-complex-v1-dev"
    assert result.official_metrics["aggregate_metrics"] == {"table_gtrm": 0.75}
    per_example = result.official_metrics["per_example_results"]
    assert isinstance(per_example, list)
    assert len(per_example) == 80


def test_frozen_subset_prepares_parsebench_result_files_and_keeps_case_ir(tmp_path: Path) -> None:
    write_tiny_pdf(tmp_path / "numeric.pdf", layout="numeric")
    item = ParseBenchCandidate(
        item_id="table/numeric",
        source_document_id="numeric-document",
        page_number=1,
        source_path="numeric.pdf",
        strata=(ParseBenchStratum.HARD_TABLE,),
    )
    manifest = ParseBenchSubsetManifest(
        dataset_id="unit-parsebench",
        split="DEVELOPMENT",
        selection_status=SubsetSelectionStatus.FROZEN,
        seed=1,
        target_count=1,
        selected_items=(item,),
        selected_item_digest=Sha256Digest(f"sha256:{'d' * 64}"),
        access_policy="unit test",
    )
    contract = load_contract_result("born-digital")

    def parse(path: Path, config: ParsingConfig) -> ParseOutcome:
        return parse_document_with_diagnostics(
            path,
            config,
            parser=ContractFixtureParser(contract),
            profile_provider=lambda _: profile_for_result(contract),
        )

    predictions = prepare_parsebench_predictions(
        manifest,
        dataset_root=tmp_path,
        export_root=tmp_path / "predictions",
        cases_root=tmp_path / "cases",
        config=ParsingConfig(parser="docling-standard"),
        parse_one=parse,
    )

    assert predictions == (tmp_path / "predictions/table/numeric.result.json",)
    payload = json.loads(predictions[0].read_text(encoding="utf-8"))
    assert payload["request"]["example_id"] == "table/numeric"
    assert payload["request"]["source_file_path"] == "numeric.pdf"
    assert payload["output"]["pages"]
    assert (tmp_path / "cases/table/numeric/document.ir.json").is_file()


def _candidates(count: int = 90) -> tuple[ParseBenchCandidate, ...]:
    strata: tuple[ParseBenchStratum, ...] = (
        ParseBenchStratum.HARD_TABLE,
        ParseBenchStratum.MERGED_CELLS,
        ParseBenchStratum.OCR_SCAN,
        ParseBenchStratum.MULTICOLUMN,
        ParseBenchStratum.DIFFICULT_LAYOUT,
        ParseBenchStratum.NUMERIC_FINANCIAL,
        ParseBenchStratum.BILINGUAL_MULTILINGUAL,
    )
    return tuple(
        ParseBenchCandidate(
            item_id=f"item-{index:03d}",
            source_document_id=f"document-{index:03d}",
            page_number=1,
            source_path=f"documents/document-{index:03d}.pdf",
            source_digest=Sha256Digest(f"sha256:{index:064x}"),
            strata=(strata[index % len(strata)],),
        )
        for index in range(count)
    )


def test_complex_subset_selection_is_deterministic_and_holdout_is_disjoint() -> None:
    candidates = _candidates()

    development, holdout = prepare_subset_manifests(candidates)
    repeated_development, repeated_holdout = prepare_subset_manifests(tuple(reversed(candidates)))

    assert development == repeated_development
    assert holdout == repeated_holdout
    assert development.selection_status is SubsetSelectionStatus.FROZEN
    assert len(development.selected_items) == 60
    assert len(holdout.selected_items) == 20
    development_documents = {item.source_document_id for item in development.selected_items}
    holdout_documents = {item.source_document_id for item in holdout.selected_items}
    assert development_documents.isdisjoint(holdout_documents)
    selected_strata = {stratum for item in development.selected_items for stratum in item.strata}
    assert all(stratum in selected_strata for stratum in ParseBenchStratum)

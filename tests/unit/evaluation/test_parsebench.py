from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
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
from docparser.evaluation.parsebench.export import (
    export_document_to_parsebench,
    parsebench_layout_label,
    write_parsebench_prediction,
)
from docparser.evaluation.parsebench.models import (
    PARSEBENCH_COMMIT,
    ParseBenchCandidate,
    ParseBenchRunRequest,
    ParseBenchStratum,
    ParseBenchSubsetManifest,
    SubsetSelectionStatus,
)
from docparser.evaluation.parsebench.runner import (
    official_evaluation_groups,
    official_evaluator_command,
    run_official_parsebench,
)
from docparser.evaluation.parsebench.subset import prepare_subset_manifests
from docparser.evaluation.parsebench.workflow import prepare_parsebench_predictions
from docparser.ir.enums import BlockType
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser


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
    assert exported.raw_output["adapter_version"] == "parsebench-export@1.1.0"


@pytest.mark.parametrize(
    ("block_type", "expected"),
    [
        (BlockType.TITLE, "title"),
        (BlockType.HEADING, "section-header"),
        (BlockType.PARAGRAPH, "text"),
        (BlockType.TABLE, "table"),
        (BlockType.FIGURE, "picture"),
        (BlockType.FIGURE_CAPTION, "caption"),
        (BlockType.HEADER, "page-header"),
        (BlockType.FOOTER, "page-footer"),
        (BlockType.PAGE_NUMBER, "text"),
        (BlockType.LIST, "text"),
        (BlockType.LIST_ITEM, "list-item"),
        (BlockType.EQUATION, "formula"),
    ],
)
def test_layout_labels_match_pinned_parsebench_v3_contract(
    block_type: BlockType,
    expected: str,
) -> None:
    contract = json.loads(
        Path("tests/fixtures/parsebench/layout-label-contract-v0.2.0.json").read_text(
            encoding="utf-8"
        )
    )

    label = parsebench_layout_label(block_type)

    assert label == expected
    assert label in contract["llamaparse_v3_accepted_labels"]


def test_every_canonical_block_type_has_an_accepted_parsebench_label() -> None:
    contract = json.loads(
        Path("tests/fixtures/parsebench/layout-label-contract-v0.2.0.json").read_text(
            encoding="utf-8"
        )
    )
    accepted = set(contract["llamaparse_v3_accepted_labels"])

    assert {parsebench_layout_label(block_type) for block_type in BlockType} <= accepted
    assert parsebench_layout_label(BlockType.UNKNOWN) == "text"


def test_export_omits_unknown_confidence_and_keeps_empty_figure_in_layout(
    tmp_path: Path,
) -> None:
    document = normalize_contract_fixture("born-digital")
    paragraph, source_figure = document.pages[0].blocks
    figure = source_figure.model_copy(
        update={"block_type": BlockType.FIGURE, "text": None, "confidence": None}
    )
    document = document.model_copy(
        update={
            "pages": (
                document.pages[0].model_copy(update={"blocks": (paragraph, figure)}),
            )
        }
    )
    exported = export_document_to_parsebench(
        document,
        example_id="layout/empty-figure",
        pipeline_name="docling-standard",
        source_file_path="layout/empty-figure.pdf",
        latency_in_ms=1,
    )

    path = write_parsebench_prediction(exported, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["output"]["layout_pages"][0]["items"]

    assert len(items) == 2
    assert items[1]["type"] == "picture"
    assert items[1]["bbox"]["label"] == "picture"
    assert "confidence" not in items[1]["bbox"]
    assert "picture" not in payload["output"]["pages"][0]["markdown"]
    assert figure.confidence is None


def test_official_runner_only_wraps_pinned_external_evaluator_output(
    tmp_path: Path,
) -> None:
    report_root = tmp_path / "report"
    export_root = tmp_path / "predictions"
    prediction_path = export_root / "table" / "example.result.json"
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        del cwd
        call = tuple(command)
        calls.append(call)
        if call == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(call, 0, stdout=f"{PARSEBENCH_COMMIT}\n", stderr="")
        group_report_root = Path(call[call.index("--report_dir") + 1])
        group_report_root.mkdir(parents=True)
        result_path = group_report_root / "_evaluation_report.json"
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
        export_root=export_root,
        report_root=report_root,
        environment_digest=Sha256Digest(f"sha256:{'a' * 64}"),
        hardware_description="unit-test-cpu",
    )
    result = run_official_parsebench(request, executor=execute)

    assert calls == [
        ("git", "rev-parse", "HEAD"),
        official_evaluator_command(
            request,
            group="table",
            report_root=report_root / "table",
        ),
    ]
    assert result.terminology == "OFFICIAL_PARSEBENCH_METRIC"
    assert result.repository_commit == PARSEBENCH_COMMIT
    assert result.subset_id == "parsebench-complex-v1-dev"
    assert result.evaluation_groups == ("table",)
    assert result.evaluator_command == calls[1]
    assert result.official_metrics["aggregate_metrics"] == {"table_gtrm": 0.75}
    per_example = result.official_metrics["per_example_results"]
    assert isinstance(per_example, list)
    assert len(per_example) == 80


def test_text_prediction_is_routed_to_content_and_formatting_official_groups(
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "predictions"
    prediction_path = export_root / "text" / "example.result.json"
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text("{}", encoding="utf-8")
    report_root = tmp_path / "report"
    evaluated_groups: list[str] = []

    def execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        del cwd
        call = tuple(command)
        if call == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(call, 0, stdout=f"{PARSEBENCH_COMMIT}\n", stderr="")
        group = call[call.index("--group") + 1]
        evaluated_groups.append(group)
        group_report_root = Path(call[call.index("--report_dir") + 1])
        group_report_root.mkdir(parents=True)
        (group_report_root / "_evaluation_report.json").write_text(
            json.dumps({"group": group, "successful": 1}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(call, 0, stdout="ok", stderr="")

    request = ParseBenchRunRequest(
        benchmark_id="official-text-contract",
        subset_id="text-contract",
        subset_manifest_digest=Sha256Digest(f"sha256:{'b' * 64}"),
        checkout_path=tmp_path,
        parsebench_python=tmp_path / "parsebench-python",
        dataset_root=tmp_path / "dataset",
        export_root=export_root,
        report_root=report_root,
        environment_digest=Sha256Digest(f"sha256:{'a' * 64}"),
        hardware_description="unit-test-cpu",
    )

    result = run_official_parsebench(request, executor=execute)

    assert official_evaluation_groups(export_root) == ("text_content", "text_formatting")
    assert evaluated_groups == ["text_content", "text_formatting"]
    assert result.official_metrics == {
        "text_content": {"group": "text_content", "successful": 1},
        "text_formatting": {"group": "text_formatting", "successful": 1},
    }


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

    parser = ContractFixtureParser(contract)

    def parse(
        path: Path,
        config: ParsingConfig,
        supplied_parser: DocumentParser,
    ) -> ParseOutcome:
        assert supplied_parser is parser
        return parse_document_with_diagnostics(
            path,
            config,
            parser=supplied_parser,
            profile_provider=lambda _: profile_for_result(contract),
        )

    predictions = prepare_parsebench_predictions(
        manifest,
        dataset_root=tmp_path,
        export_root=tmp_path / "predictions",
        cases_root=tmp_path / "cases",
        config=ParsingConfig(parser="docling-standard"),
        parse_one=parse,
        parser_factory=lambda _: parser,
    )

    assert predictions == (tmp_path / "predictions/table/numeric.result.json",)
    payload = json.loads(predictions[0].read_text(encoding="utf-8"))
    assert payload["request"]["example_id"] == "table/numeric"
    assert payload["request"]["source_file_path"] == "numeric.pdf"
    assert payload["output"]["pages"]
    assert (tmp_path / "cases/table/numeric/document.ir.json").is_file()

    def fail_factory(config: ParsingConfig) -> ContractFixtureParser:
        del config
        raise AssertionError("parser must not load")

    reexported = prepare_parsebench_predictions(
        manifest,
        dataset_root=tmp_path,
        export_root=tmp_path / "reexported",
        cases_root=tmp_path / "cases",
        config=ParsingConfig(parser="docling-standard"),
        parser_factory=fail_factory,
        reuse_saved_ir=True,
    )
    assert reexported == (tmp_path / "reexported/table/numeric.result.json",)


def test_manifest_batch_reuses_one_parser_instance(tmp_path: Path) -> None:
    write_tiny_pdf(tmp_path / "shared.pdf", layout="numeric")
    items = tuple(
        ParseBenchCandidate(
            item_id=f"table/item-{index:02d}",
            source_document_id=f"document-{index:02d}",
            page_number=1,
            source_path="shared.pdf",
            strata=(ParseBenchStratum.HARD_TABLE,),
        )
        for index in range(12)
    )
    manifest = ParseBenchSubsetManifest(
        dataset_id="unit-batch",
        split="DEVELOPMENT",
        selection_status=SubsetSelectionStatus.FROZEN,
        seed=1,
        target_count=12,
        selected_items=items,
        selected_item_digest=Sha256Digest(f"sha256:{'d' * 64}"),
        access_policy="unit test",
    )
    contract = load_contract_result("born-digital")
    parser = ContractFixtureParser(contract)
    factory_calls = 0
    parse_calls = 0

    def parser_factory(config: ParsingConfig) -> ContractFixtureParser:
        nonlocal factory_calls
        del config
        factory_calls += 1
        return parser

    def parse(
        path: Path,
        config: ParsingConfig,
        supplied_parser: DocumentParser,
    ) -> ParseOutcome:
        nonlocal parse_calls
        assert supplied_parser is parser
        parse_calls += 1
        return parse_document_with_diagnostics(
            path,
            config,
            parser=supplied_parser,
            profile_provider=lambda _: profile_for_result(contract),
        )

    predictions = prepare_parsebench_predictions(
        manifest,
        dataset_root=tmp_path,
        export_root=tmp_path / "predictions",
        cases_root=tmp_path / "cases",
        config=ParsingConfig(parser="docling-standard"),
        parse_one=parse,
        parser_factory=parser_factory,
    )

    assert len(predictions) == 12
    assert predictions == tuple(
        tmp_path / f"predictions/table/item-{index:02d}.result.json"
        for index in range(12)
    )
    assert factory_calls == 1
    assert parse_calls == 12


def test_manifest_parser_exception_fails_the_prediction_run_explicitly(tmp_path: Path) -> None:
    item = ParseBenchCandidate(
        item_id="table/failure",
        source_document_id="failure-document",
        page_number=1,
        source_path="failure.pdf",
        strata=(ParseBenchStratum.HARD_TABLE,),
    )
    manifest = ParseBenchSubsetManifest(
        dataset_id="unit-failure",
        split="DEVELOPMENT",
        selection_status=SubsetSelectionStatus.FROZEN,
        seed=1,
        target_count=1,
        selected_items=(item,),
        selected_item_digest=Sha256Digest(f"sha256:{'d' * 64}"),
        access_policy="unit test",
    )
    parser = ContractFixtureParser(load_contract_result("born-digital"))

    def fail(
        path: Path,
        config: ParsingConfig,
        supplied_parser: DocumentParser,
    ) -> ParseOutcome:
        del path, config, supplied_parser
        raise RuntimeError("intentional parser failure")

    with pytest.raises(RuntimeError, match="intentional parser failure"):
        prepare_parsebench_predictions(
            manifest,
            dataset_root=tmp_path,
            export_root=tmp_path / "predictions",
            cases_root=tmp_path / "cases",
            config=ParsingConfig(parser="docling-standard"),
            parse_one=fail,
            parser_factory=lambda _: parser,
        )


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

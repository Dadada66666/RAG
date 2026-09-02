from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tests.parser_fixture import normalize_contract_fixture

from docparser.evaluation.parsebench.export import export_document_to_parsebench
from docparser.evaluation.parsebench.models import (
    PARSEBENCH_COMMIT,
    ParseBenchCandidate,
    ParseBenchRunRequest,
    ParseBenchStratum,
    SubsetSelectionStatus,
)
from docparser.evaluation.parsebench.runner import run_official_parsebench
from docparser.evaluation.parsebench.subset import prepare_subset_manifests
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
    result_path = tmp_path / "official-result.json"
    calls: list[tuple[str, ...]] = []

    def execute(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        del cwd
        call = tuple(command)
        calls.append(call)
        if call == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(call, 0, stdout=f"{PARSEBENCH_COMMIT}\n", stderr="")
        result_path.write_text(json.dumps({"table_gtrm": 0.75}), encoding="utf-8")
        return subprocess.CompletedProcess(call, 0, stdout="ok", stderr="")

    result = run_official_parsebench(
        ParseBenchRunRequest(
            benchmark_id="official-parsebench-smoke-v1",
            subset_id="parsebench-complex-v1-dev",
            subset_manifest_digest=Sha256Digest(f"sha256:{'b' * 64}"),
            checkout_path=tmp_path,
            evaluator_command=("uv", "run", "parse-bench", "evaluate"),
            official_result_path=result_path,
            dataset_root=tmp_path / "dataset",
            export_root=tmp_path / "predictions",
            environment_digest=Sha256Digest(f"sha256:{'a' * 64}"),
            hardware_description="unit-test-cpu",
        ),
        executor=execute,
    )

    assert calls == [
        ("git", "rev-parse", "HEAD"),
        ("uv", "run", "parse-bench", "evaluate"),
    ]
    assert result.terminology == "OFFICIAL_PARSEBENCH_METRIC"
    assert result.repository_commit == PARSEBENCH_COMMIT
    assert result.subset_id == "parsebench-complex-v1-dev"
    assert result.official_metrics == {"table_gtrm": 0.75}


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

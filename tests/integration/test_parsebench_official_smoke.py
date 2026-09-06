from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.parser_fixture import normalize_contract_fixture
from tests.pdf_factory import write_tiny_pdf

from docparser.evaluation.parsebench.export import (
    export_document_to_parsebench,
    write_parsebench_prediction,
)
from docparser.evaluation.parsebench.models import PARSEBENCH_COMMIT, ParseBenchRunRequest
from docparser.evaluation.parsebench.runner import run_official_parsebench
from docparser.ir.types import Sha256Digest


@pytest.mark.integration
@pytest.mark.parser
def test_pinned_official_parsebench_evaluator_accepts_project_export(tmp_path: Path) -> None:
    checkout_value = os.getenv("DOCPARSER_PARSEBENCH_CHECKOUT")
    python_value = os.getenv("DOCPARSER_PARSEBENCH_PYTHON")
    if not checkout_value or not python_value:
        pytest.skip("pinned ParseBench checkout/runtime not configured")

    test_cases = tmp_path / "test-cases"
    group = test_cases / "text"
    group.mkdir(parents=True)
    write_tiny_pdf(group / "known-answer.pdf", layout="text")
    expected_markdown = "# Annual Report\n\nRevenue increased during the year."
    for category in ("text_content", "text_formatting"):
        (test_cases / f"{category}.jsonl").write_text(
            json.dumps(
                {
                    "pdf": "text/known-answer.pdf",
                    "category": category,
                    "type": "expected_markdown",
                    "expected_markdown": expected_markdown,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    prediction_root = tmp_path / "predictions"
    prediction = export_document_to_parsebench(
        normalize_contract_fixture("born-digital"),
        example_id="text/known-answer",
        pipeline_name="docling-standard",
        source_file_path="text/known-answer.pdf",
        latency_in_ms=1,
    )
    write_parsebench_prediction(prediction, prediction_root)

    report = run_official_parsebench(
        ParseBenchRunRequest(
            benchmark_id="official-parsebench-known-answer",
            subset_id="known-answer",
            subset_manifest_digest=Sha256Digest(f"sha256:{'b' * 64}"),
            checkout_path=Path(checkout_value),
            parsebench_python=Path(python_value),
            dataset_root=test_cases,
            export_root=prediction_root,
            report_root=tmp_path / "report",
            environment_digest=Sha256Digest(f"sha256:{'a' * 64}"),
            hardware_description="integration-test",
            repository_commit=PARSEBENCH_COMMIT,
        )
    )

    content_report = report.official_metrics["text_content"]
    formatting_report = report.official_metrics["text_formatting"]
    assert isinstance(content_report, dict)
    assert isinstance(formatting_report, dict)
    assert content_report["total_examples"] == 1
    assert content_report["successful"] == 1
    assert formatting_report["total_examples"] == 1
    assert formatting_report["successful"] == 1

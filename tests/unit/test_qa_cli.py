import json
from pathlib import Path

import pytest
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document
from tests.unit.test_evidence_qa import CitingModel
from typer.testing import CliRunner

from docparser.cli.main import app
from docparser.ir.serialization import dump_canonical_json


def test_batch_command_preserves_failed_question_and_manifest_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.application.test_qa_batch import FailsSecondQuestion, questions

    from docparser.retrieval.index import build_evidence_index

    runtime = FakeEmbeddingRuntime()
    build_evidence_index((make_retrieval_document(),), runtime, tmp_path / "index")
    monkeypatch.setattr("docparser.cli.main.BgeM3Runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(
        "docparser.cli.main.SiliconFlowChatModel", lambda config: FailsSecondQuestion()
    )
    query_path = tmp_path / "questions.jsonl"
    query_path.write_text(
        "\n".join(item.model_dump_json() for item in questions()), encoding="utf-8"
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "rag-batch",
            "--questions",
            str(query_path),
            "--index",
            str(tmp_path / "index"),
            "--model-path",
            str(tmp_path),
            "--output",
            str(tmp_path / "run"),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "completed 3 requests" in result.output
    failed = json.loads((tmp_path / "run" / "00001.qa.json").read_text())
    assert failed["execution_error"]["code"] == "TRANSPORT_ERROR"
    truth = tmp_path / "truth.jsonl"
    truth.write_text(
        "\n".join(
            json.dumps(
                {
                    "query_id": str(item.query_id),
                    "answerable": True,
                    "answer_correct": number != 1,
                    "citations_support_all_claims": number != 1,
                    "annotator": "fixture",
                }
            )
            for number, item in enumerate(questions())
        ),
        encoding="utf-8",
    )
    evaluated = runner.invoke(
        app,
        [
            "rag-evaluate",
            "--results",
            str(tmp_path / "run"),
            "--judgments",
            str(truth),
            "--output",
            str(tmp_path / "metrics.json"),
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["denominator_scope"] == "QUESTION_MANIFEST"
    assert metrics["question_count"] == 3 and metrics["execution_error_count"] == 1


def test_index_ask_context_and_evaluation_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    runtime = FakeEmbeddingRuntime()
    monkeypatch.setattr("docparser.cli.main.BgeM3Runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr("docparser.cli.main.SiliconFlowChatModel", lambda config: CitingModel())
    root = tmp_path / "ir"
    root.mkdir()
    (root / "document.ir.json").write_bytes(dump_canonical_json(make_retrieval_document()))
    index = tmp_path / "index"
    indexed = runner.invoke(
        app,
        ["rag-index", "--ir-root", str(root), "--model-path", str(root), "--output", str(index)],
    )
    assert indexed.exit_code == 0, indexed.output
    results = tmp_path / "results"
    output = results / "revenue.qa.json"
    arguments = [
        "rag-ask",
        "Revenue?",
        "--index",
        str(index),
        "--model-path",
        str(root),
        "--output",
        str(output),
    ]
    asked = runner.invoke(app, arguments)
    assert asked.exit_code == 0, asked.output
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["answer"]["status"] == "ANSWERED"
    assert result["index_manifest"]["chunker_version"] == "ir-fixed-token@1.1.0"
    judgment = tmp_path / "judgments.jsonl"
    judgment.write_text(
        json.dumps(
            {
                "query_id": result["retrieval"]["benchmark_query_id"],
                "answerable": True,
                "answer_correct": True,
                "citations_support_all_claims": True,
                "annotator": "fixture",
            }
        ),
        encoding="utf-8",
    )
    evaluated = runner.invoke(
        app,
        [
            "rag-evaluate",
            "--results",
            str(results),
            "--judgments",
            str(judgment),
            "--output",
            str(tmp_path / "metrics.json"),
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    assert json.loads((tmp_path / "metrics.json").read_text())["correct_and_supported_rate"] == 1.0
    local = runner.invoke(app, [*arguments, "--context-only", "--no-expand-context"])
    assert local.exit_code == 0, local.output
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["answer"] is None
    assert result["context"]["config"]["expand_source_tokens"] == 0
    assert result["context"]["config"]["include_related"] is False
    m2 = runner.invoke(app, [*arguments, "--context-only", "--table-context"])
    assert m2.exit_code == 0, m2.output
    restored = json.loads(output.read_text(encoding="utf-8"))
    assert restored["context"]["config"]["table_policy"] == "LOGICAL_ROWS"
    assert restored["retrieval"] == result["retrieval"]


def test_invalid_citations_exit_nonzero_and_keep_auditable_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docparser.retrieval.index import build_evidence_index

    runtime = FakeEmbeddingRuntime()
    build_evidence_index((make_retrieval_document(),), runtime, tmp_path / "index")
    monkeypatch.setattr("docparser.cli.main.BgeM3Runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(
        "docparser.cli.main.SiliconFlowChatModel", lambda config: CitingModel("E999")
    )
    output = tmp_path / "bad.qa.json"
    result = CliRunner().invoke(
        app,
        [
            "rag-ask",
            "Revenue?",
            "--index",
            str(tmp_path / "index"),
            "--model-path",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 3
    assert json.loads(output.read_text())["answer"]["status"] == "INVALID_RESPONSE"

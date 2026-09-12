import json
from pathlib import Path

import pytest
from tests.parser_fixture import load_contract_result, normalization_context, profile_for_result
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document
from tests.unit.test_evidence_qa import CitingModel

from docparser.application.qa import ask_document
from docparser.application.qa_batch import QABatchConfig, QAQuestion, load_qa_batch, run_qa_batch
from docparser.evaluation.qa import QAJudgment, evaluate_qa
from docparser.normalization.neutral import normalize_neutral_result
from docparser.retrieval.answering import ChatRuntimeError, Completion
from docparser.retrieval.index import build_evidence_index


class FailsSecondQuestion(CitingModel):
    def __init__(self, *, unexpected: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.unexpected = unexpected

    def complete(self, system: str, user: str) -> Completion:
        self.calls += 1
        if self.calls == 2:
            if self.unexpected:
                raise ValueError("fixture internal bug")
            raise ChatRuntimeError("TRANSPORT_ERROR", "fixture timeout")
        return super().complete(system, user)


def questions() -> tuple[QAQuestion, ...]:
    # The same wording may represent independent items; preserve supplied identities.
    return tuple(QAQuestion(query_id=f"query-{number}", question="Revenue?") for number in range(3))


def test_document_scope_prefilters_candidates_and_preserves_explicit_query_id(
    tmp_path: Path,
) -> None:
    neutral = load_contract_result("born-digital")
    other = normalize_neutral_result(neutral, normalization_context(profile_for_result(neutral)))
    documents = (make_retrieval_document(), other)
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index(documents, runtime, tmp_path).session(runtime)
    first = session.retrieve("Revenue?", top_k=1).hits[0]
    winner = next(chunk.document_id for chunk in session.chunks if chunk.chunk_id == first.chunk_id)
    selected = next(
        str(document.document_id) for document in documents if document.document_id != winner
    )
    session.index.manifest = session.index.manifest.model_copy(
        update={
            "warnings": ("unselected-document warning",),
            "document_warnings": {str(winner): ("unselected-document warning",)},
        }
    )
    result = ask_document(
        "Revenue?", session, top_k=1, document_ids=(selected,), query_id="external-id"
    )
    assert result.retrieval.benchmark_query_id == "external-id"
    assert len(result.retrieval.hits) == 1  # Postfiltering the global Top-1 would be empty.
    assert result.document_ids == (selected,)
    assert {source.document_id for source in result.context.sources} == {selected}
    assert "unselected-document warning" not in result.context.warnings
    scoped = session.retrieve("Revenue?", document_ids=(selected,))
    assert scoped.benchmark_query_id != session.retrieve("Revenue?").benchmark_query_id
    calls = len(runtime.calls)
    with pytest.raises(ValueError, match="not present"):
        session.retrieve("Revenue?", document_ids=("missing",))
    assert len(runtime.calls) == calls


def test_batch_keeps_provider_failure_and_denominator_and_reuses_embeddings(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index(
        (make_retrieval_document(),), runtime, tmp_path / "index"
    ).session(runtime)
    model = FailsSecondQuestion()
    run_qa_batch(questions(), session, model, tmp_path / "run", QABatchConfig())
    manifest, results = load_qa_batch(tmp_path / "run")
    assert manifest.status == "COMPLETE" and model.calls == 3
    assert len(runtime.calls) == 4  # Corpus once, each query once.
    assert [result.retrieval.benchmark_query_id for result in results] == [
        item.query_id for item in questions()
    ]
    failed = results[1]
    assert failed.execution_error is not None and failed.execution_error.code == "TRANSPORT_ERROR"
    assert failed.answer is None and failed.context.evidence and failed.retrieval.hits
    assert results[2].answer is not None
    truth = tuple(
        QAJudgment(
            query_id=str(item.query_id),
            answerable=True,
            answer_correct=number != 1,
            citations_support_all_claims=number != 1,
            annotator="synthetic-counter-test",
        )
        for number, item in enumerate(questions())
    )
    metrics = evaluate_qa(results, truth, questions=manifest.questions)
    assert metrics.denominator_scope == "QUESTION_MANIFEST"
    assert metrics.question_count == 3 and metrics.execution_error_count == 1
    assert metrics.answer_coverage == metrics.correct_and_supported_rate == 2 / 3
    with pytest.raises(ValueError, match="planned"):
        evaluate_qa(results[:1], truth[:1], questions=manifest.questions)
    with pytest.raises(FileExistsError):
        run_qa_batch(questions(), session, model, tmp_path / "run", QABatchConfig())
    assert model.calls == 3


def test_programming_failure_leaves_incomplete_run_instead_of_faking_completion(
    tmp_path: Path,
) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index(
        (make_retrieval_document(),), runtime, tmp_path / "index"
    ).session(runtime)
    with pytest.raises(ValueError, match="internal bug"):
        run_qa_batch(
            questions(),
            session,
            FailsSecondQuestion(unexpected=True),
            tmp_path / "run",
            QABatchConfig(),
        )
    assert (tmp_path / "run" / "00000.qa.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        load_qa_batch(tmp_path / "run")


@pytest.mark.parametrize("change", ["missing", "modified"])
def test_completed_run_rejects_missing_or_changed_result(tmp_path: Path, change: str) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index(
        (make_retrieval_document(),), runtime, tmp_path / "index"
    ).session(runtime)
    run_qa_batch(questions(), session, CitingModel(), tmp_path / "run", QABatchConfig())
    result_path = tmp_path / "run" / "00001.qa.json"
    if change == "missing":
        result_path.unlink()
    else:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["question"] = "a different question"
        result_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="differ|changed"):
        load_qa_batch(tmp_path / "run")


def test_batch_rejects_unknown_scope_before_calls_or_outputs(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index(
        (make_retrieval_document(),), runtime, tmp_path / "index"
    ).session(runtime)
    bad = (QAQuestion(query_id="q", question="Revenue?", document_ids=("missing",)),)
    with pytest.raises(ValueError, match="document IDs"):
        run_qa_batch(bad, session, CitingModel(), tmp_path / "run", QABatchConfig())
    assert not (tmp_path / "run").exists()
    assert len(runtime.calls) == 1

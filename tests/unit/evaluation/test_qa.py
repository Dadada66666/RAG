from pathlib import Path

import pytest
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document
from tests.unit.test_evidence_qa import CitingModel

from docparser.application.qa import ask_document
from docparser.evaluation.qa import QAJudgment, evaluate_qa
from docparser.retrieval.context import ContextConfig
from docparser.retrieval.index import build_evidence_index


def test_answer_quality_denominator_includes_abstentions(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    supported = ask_document("Revenue?", session, model=CitingModel())
    abstained = ask_document(
        "Missing information?",
        session,
        model=CitingModel(),
        context_config=ContextConfig(max_tokens=1),
    )
    truth = (
        QAJudgment(
            query_id=supported.retrieval.benchmark_query_id,
            answerable=True,
            answer_correct=True,
            citations_support_all_claims=True,
            annotator="fixture",
        ),
        QAJudgment(
            query_id=abstained.retrieval.benchmark_query_id,
            answerable=False,
            answer_correct=False,
            citations_support_all_claims=False,
            annotator="fixture",
        ),
    )
    metrics = evaluate_qa((supported, abstained), truth)
    assert metrics.answer_coverage == 0.5
    assert metrics.correct_and_supported_rate == 0.5
    assert metrics.correct_and_supported_among_answered == 1.0
    assert metrics.abstention_rate_on_unanswerable == 1.0
    with pytest.raises(ValueError, match="exactly one"):
        evaluate_qa((supported, abstained), truth[:1])


def test_exact_quote_membership_does_not_automatically_count_as_supported(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    result = ask_document("Revenue?", session, model=CitingModel())
    judgment = QAJudgment(
        query_id=result.retrieval.benchmark_query_id,
        answerable=True,
        answer_correct=False,
        citations_support_all_claims=False,
        annotator="independent-human",
    )
    metrics = evaluate_qa((result,), (judgment,))
    assert metrics.correct_and_supported_rate == 0.0
    assert metrics.unsupported_answer_rate == 1.0

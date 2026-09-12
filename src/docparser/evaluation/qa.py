"""Answer/citation evaluation from independent judgments, separate from page retrieval hits."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from docparser.application.qa import QAResult
from docparser.application.qa_batch import QAQuestion
from docparser.ir.base import StrictIRModel


class QAJudgment(StrictIRModel):
    query_id: str
    answerable: bool
    answer_correct: bool
    citations_support_all_claims: bool
    annotator: str = Field(min_length=1)


class QAMetrics(StrictIRModel):
    denominator_scope: Literal["RECORDED_RESULTS", "QUESTION_MANIFEST"]
    question_count: int
    answered_count: int
    invalid_response_count: int
    execution_error_count: int
    answer_coverage: float
    correct_and_supported_rate: float
    correct_and_supported_among_answered: float | None
    unsupported_answer_rate: float
    unanswerable_count: int
    abstention_rate_on_unanswerable: float | None


def evaluate_qa(
    results: tuple[QAResult, ...],
    judgments: tuple[QAJudgment, ...],
    *,
    questions: tuple[QAQuestion, ...] | None = None,
) -> QAMetrics:
    if not results:
        raise ValueError("QA evaluation requires results")
    result_ids = [result.retrieval.benchmark_query_id for result in results]
    truth = {item.query_id: item for item in judgments}
    if len(set(result_ids)) != len(result_ids) or len(truth) != len(judgments):
        raise ValueError("duplicate QA result or judgment IDs")
    if set(result_ids) != set(truth):
        raise ValueError("every QA result needs exactly one independent judgment")
    if questions is not None:
        planned = {item.query_id: item for item in questions}
        if len(planned) != len(questions) or set(planned) != set(result_ids):
            raise ValueError("all planned questions must have exactly one result and judgment")
        for result in results:
            item = planned[result.retrieval.benchmark_query_id]
            if result.question != item.question or result.document_ids != tuple(
                sorted(set(item.document_ids))
            ):
                raise ValueError("result question or document scope differs from planned request")
    answered = correct_supported = unsupported = unanswerable = abstained = invalid = errors = 0
    for result in results:
        answer = result.answer
        if answer is None and result.execution_error is None:
            raise ValueError("context-only output is not an answer-quality result")
        judgment = truth[result.retrieval.benchmark_query_id]
        unanswerable += not judgment.answerable
        if result.execution_error is not None:
            if (
                answer is not None
                or judgment.answer_correct
                or judgment.citations_support_all_claims
            ):
                raise ValueError("an execution failure cannot be a correct supported answer")
            errors += 1
            continue
        assert answer is not None
        invalid += answer.status == "INVALID_RESPONSE"
        if answer.status == "ANSWERED":
            answered += 1
            correct_supported += judgment.answer_correct and judgment.citations_support_all_claims
            unsupported += not judgment.citations_support_all_claims
        else:
            if judgment.answer_correct or judgment.citations_support_all_claims:
                raise ValueError("a non-answer cannot be annotated as a correct supported answer")
            abstained += answer.status == "INSUFFICIENT_EVIDENCE" and not judgment.answerable
    count = len(results)
    return QAMetrics(
        denominator_scope="QUESTION_MANIFEST" if questions is not None else "RECORDED_RESULTS",
        question_count=count,
        answered_count=answered,
        invalid_response_count=invalid,
        execution_error_count=errors,
        answer_coverage=answered / count,
        correct_and_supported_rate=correct_supported / count,
        correct_and_supported_among_answered=correct_supported / answered if answered else None,
        unsupported_answer_rate=unsupported / count,
        unanswerable_count=unanswerable,
        abstention_rate_on_unanswerable=abstained / unanswerable if unanswerable else None,
    )

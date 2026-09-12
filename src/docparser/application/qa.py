"""Retrieve, construct source context and answer without reranking or reparsing."""

from __future__ import annotations

from time import perf_counter

from docparser.ir.base import StrictIRModel
from docparser.retrieval.answering import (
    ChatModel,
    ChatRuntimeError,
    GroundedAnswer,
    answer_from_context,
)
from docparser.retrieval.context import ContextConfig, EvidenceContext
from docparser.retrieval.dense import QueryRetrieval
from docparser.retrieval.index import IndexManifest, QASearchSession


class QAExecutionError(StrictIRModel):
    code: str
    message: str


class QAResult(StrictIRModel):
    question: str
    document_ids: tuple[str, ...] = ()
    index_manifest: IndexManifest
    retrieval: QueryRetrieval
    context: EvidenceContext
    answer: GroundedAnswer | None
    execution_error: QAExecutionError | None = None
    elapsed_seconds: dict[str, float]


def ask_document(
    question: str,
    session: QASearchSession,
    *,
    model: ChatModel | None = None,
    top_k: int = 5,
    context_config: ContextConfig | None = None,
    document_ids: tuple[str, ...] = (),
    query_id: str | None = None,
) -> QAResult:
    start = perf_counter()
    retrieval = session.retrieve(
        question, top_k=top_k, document_ids=document_ids, query_id=query_id
    )
    retrieved = perf_counter()
    context = session.context(retrieval, context_config, document_ids=document_ids)
    prepared = perf_counter()
    error = None
    answer = None
    if model is not None:
        try:
            answer = answer_from_context(question, context, model)
        except ChatRuntimeError as failure:
            error = QAExecutionError(code=failure.code, message=str(failure))
    return QAResult(
        question=question,
        document_ids=tuple(sorted(set(document_ids))),
        index_manifest=session.index.manifest,
        retrieval=retrieval,
        context=context,
        answer=answer,
        execution_error=error,
        elapsed_seconds={
            "retrieval": retrieved - start,
            "context": prepared - retrieved,
            "answer": perf_counter() - prepared,
        },
    )

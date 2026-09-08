from __future__ import annotations

from tests.ir_factory import TEST_NAMESPACE

from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import (
    RetrievalSlice,
    evaluate_page_retrieval,
    evaluate_table_source_exposure,
)
from docparser.ir.ids import ChunkId, generate_uuid5_id
from docparser.retrieval.dense import QueryRetrieval, RetrievedChunk


def _truth(
    identifier: str, evidence_type: RetrievalEvidenceType, page_index: int
) -> RetrievalGroundTruth:
    return RetrievalGroundTruth(
        benchmark_query_id=identifier,
        source_dataset="synthetic",
        source_dataset_item_id=f"source-{identifier}",
        document_name="academic/test",
        question=f"question {identifier}",
        answer="answer",
        evidence_type=evidence_type,
        evidence_contexts=("context",),
        evidence_page_indices=(page_index,),
        document_type_or_domain="academic",
    )


def _hit(
    query_id: str,
    pages: tuple[int, ...],
    rank: int,
    *,
    document: str = "academic/test",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=generate_uuid5_id(ChunkId, TEST_NAMESPACE, query_id, str(rank)),
        document_name=document,
        rank=rank,
        score=1.0 / rank,
        page_numbers=pages,
    )


def test_ohr_page_indices_are_shifted_once_and_metrics_keep_slice_denominators() -> None:
    truths = (
        _truth("text", RetrievalEvidenceType.TEXT, 0),
        _truth("table", RetrievalEvidenceType.TABLE, 1),
        _truth("order", RetrievalEvidenceType.READING_ORDER, 2),
    )
    retrievals = (
        QueryRetrieval(
            benchmark_query_id="text",
            document_name="academic/test",
            hits=(_hit("text", (1,), 1),),
        ),
        QueryRetrieval(
            benchmark_query_id="table",
            document_name="academic/test",
            hits=(_hit("table-wrong", (1,), 1), _hit("table", (2,), 2)),
        ),
        QueryRetrieval(
            benchmark_query_id="order",
            document_name="academic/test",
            hits=(_hit("wrong-doc", (3,), 1, document="news/other"),),
        ),
    )

    report = evaluate_page_retrieval(truths, retrievals)
    all_metrics = report.metrics_by_slice[RetrievalSlice.ALL]
    assert all_metrics.query_count == 3
    assert all_metrics.page_hit_rate_at_1 == 1 / 3
    assert all_metrics.page_hit_rate_at_5 == 2 / 3
    assert all_metrics.page_hit_rate_at_10 == 2 / 3
    assert all_metrics.mrr == 0.5
    assert report.metrics_by_slice[RetrievalSlice.TEXT].page_hit_rate_at_1 == 1.0
    assert report.metrics_by_slice[RetrievalSlice.TABLE].mrr == 0.5
    assert report.metrics_by_slice[RetrievalSlice.READING_ORDER].mrr == 0.0


def test_missing_retrieval_output_is_a_zero_not_an_exclusion() -> None:
    report = evaluate_page_retrieval(
        (_truth("missing", RetrievalEvidenceType.TEXT, 0),),
        (),
    )

    metrics = report.metrics_by_slice[RetrievalSlice.ALL]
    assert metrics.query_count == 1
    assert metrics.hit_count_at_10 == 0
    assert metrics.mrr == 0.0


def test_table_source_exposure_is_distinct_from_same_page_hit() -> None:
    truth = _truth("table-exposure", RetrievalEvidenceType.TABLE, 0)
    paragraph_hit = _hit("paragraph", (1,), 1)
    table_hit = _hit("table-source", (1,), 2)
    retrieval = QueryRetrieval(
        benchmark_query_id=truth.benchmark_query_id,
        document_name=truth.document_name,
        hits=(paragraph_hit, table_hit),
    )

    page_metrics = evaluate_page_retrieval((truth,), (retrieval,))
    no_table = evaluate_table_source_exposure((truth,), (retrieval,), frozenset())
    with_table = evaluate_table_source_exposure(
        (truth,), (retrieval,), frozenset({table_hit.chunk_id})
    )

    assert page_metrics.metrics_by_slice[RetrievalSlice.TABLE].page_hit_rate_at_1 == 1.0
    assert no_table.metrics.table_source_exposure_at_1 == 0.0
    assert no_table.metrics.table_source_exposure_at_5 == 0.0
    assert with_table.metrics.table_source_exposure_at_1 == 0.0
    assert with_table.metrics.table_source_exposure_at_5 == 1.0

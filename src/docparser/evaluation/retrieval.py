"""OHR page-level retrieval evaluation over chunk provenance."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.ir.base import StrictIRModel
from docparser.ir.ids import ChunkId
from docparser.retrieval.dense import QueryRetrieval


class RetrievalSlice(StrEnum):
    ALL = "ALL"
    TEXT = "TEXT"
    TABLE = "TABLE"
    READING_ORDER = "READING_ORDER"


class PageRetrievalMetrics(StrictIRModel):
    query_count: int = Field(strict=True, ge=0)
    hit_count_at_1: int = Field(strict=True, ge=0)
    hit_count_at_5: int = Field(strict=True, ge=0)
    hit_count_at_10: int = Field(strict=True, ge=0)
    page_hit_rate_at_1: float | None
    page_hit_rate_at_5: float | None
    page_hit_rate_at_10: float | None
    reciprocal_rank_sum: float
    mrr: float | None


class PageRetrievalReport(StrictIRModel):
    evaluator_version: str = "ohr-page-retrieval@1.0.0"
    metrics_by_slice: dict[RetrievalSlice, PageRetrievalMetrics]


class TableSourceExposureMetrics(StrictIRModel):
    query_count: int = Field(strict=True, ge=0)
    exposure_count_at_1: int = Field(strict=True, ge=0)
    exposure_count_at_5: int = Field(strict=True, ge=0)
    exposure_count_at_10: int = Field(strict=True, ge=0)
    table_source_exposure_at_1: float | None
    table_source_exposure_at_5: float | None
    table_source_exposure_at_10: float | None


class TableSourceExposureReport(StrictIRModel):
    evaluator_version: str = "ohr-table-source-exposure@1.0.0"
    metrics: TableSourceExposureMetrics


def _first_page_hit_rank(
    truth: RetrievalGroundTruth, result: QueryRetrieval | None
) -> int | None:
    if result is None:
        return None
    gold_pages = {index + 1 for index in truth.evidence_page_indices}
    for hit in result.hits:
        if hit.document_name == truth.document_name and gold_pages.intersection(hit.page_numbers):
            return hit.rank
    return None


def _metrics(
    truths: tuple[RetrievalGroundTruth, ...],
    results: dict[str, QueryRetrieval],
) -> PageRetrievalMetrics:
    ranks = tuple(
        _first_page_hit_rank(truth, results.get(truth.benchmark_query_id)) for truth in truths
    )
    count = len(truths)
    hit_1 = sum(rank is not None and rank <= 1 for rank in ranks)
    hit_5 = sum(rank is not None and rank <= 5 for rank in ranks)
    hit_10 = sum(rank is not None and rank <= 10 for rank in ranks)
    reciprocal_rank_sum = sum(1.0 / rank for rank in ranks if rank is not None)
    return PageRetrievalMetrics(
        query_count=count,
        hit_count_at_1=hit_1,
        hit_count_at_5=hit_5,
        hit_count_at_10=hit_10,
        page_hit_rate_at_1=hit_1 / count if count else None,
        page_hit_rate_at_5=hit_5 / count if count else None,
        page_hit_rate_at_10=hit_10 / count if count else None,
        reciprocal_rank_sum=reciprocal_rank_sum,
        mrr=reciprocal_rank_sum / count if count else None,
    )


def evaluate_page_retrieval(
    truths: tuple[RetrievalGroundTruth, ...],
    retrievals: tuple[QueryRetrieval, ...],
) -> PageRetrievalReport:
    """Evaluate page hits, explicitly translating OHR 0-based indices to IR pages."""

    truth_ids = [truth.benchmark_query_id for truth in truths]
    if len(truth_ids) != len(set(truth_ids)):
        raise ValueError("retrieval truth benchmark_query_id values must be unique")
    result_ids = [result.benchmark_query_id for result in retrievals]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("retrieval result benchmark_query_id values must be unique")
    unknown = sorted(set(result_ids) - set(truth_ids))
    if unknown:
        raise ValueError(f"retrieval results contain unknown query ID: {unknown[0]}")
    by_id = {result.benchmark_query_id: result for result in retrievals}
    slices = {
        RetrievalSlice.ALL: truths,
        RetrievalSlice.TEXT: tuple(
            truth for truth in truths if truth.evidence_type is RetrievalEvidenceType.TEXT
        ),
        RetrievalSlice.TABLE: tuple(
            truth for truth in truths if truth.evidence_type is RetrievalEvidenceType.TABLE
        ),
        RetrievalSlice.READING_ORDER: tuple(
            truth
            for truth in truths
            if truth.evidence_type is RetrievalEvidenceType.READING_ORDER
        ),
    }
    return PageRetrievalReport(
        metrics_by_slice={name: _metrics(items, by_id) for name, items in slices.items()}
    )


def evaluate_table_source_exposure(
    truths: tuple[RetrievalGroundTruth, ...],
    retrievals: tuple[QueryRetrieval, ...],
    table_exposing_chunk_ids: frozenset[ChunkId],
) -> TableSourceExposureReport:
    """Measure whether TABLE queries expose any table-derived source in Top-K."""

    table_truths = tuple(
        truth for truth in truths if truth.evidence_type is RetrievalEvidenceType.TABLE
    )
    by_id = {result.benchmark_query_id: result for result in retrievals}

    def exposed(truth: RetrievalGroundTruth, cutoff: int) -> bool:
        result = by_id.get(truth.benchmark_query_id)
        return result is not None and any(
            hit.rank <= cutoff
            and hit.document_name == truth.document_name
            and hit.chunk_id in table_exposing_chunk_ids
            for hit in result.hits
        )

    count = len(table_truths)
    exposed_1 = sum(exposed(truth, 1) for truth in table_truths)
    exposed_5 = sum(exposed(truth, 5) for truth in table_truths)
    exposed_10 = sum(exposed(truth, 10) for truth in table_truths)
    return TableSourceExposureReport(
        metrics=TableSourceExposureMetrics(
            query_count=count,
            exposure_count_at_1=exposed_1,
            exposure_count_at_5=exposed_5,
            exposure_count_at_10=exposed_10,
            table_source_exposure_at_1=exposed_1 / count if count else None,
            table_source_exposure_at_5=exposed_5 / count if count else None,
            table_source_exposure_at_10=exposed_10 / count if count else None,
        )
    )

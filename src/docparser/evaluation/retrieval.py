"""Frozen chunk-gold and diagnostic page-level retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.ir.base import StrictIRModel
from docparser.ir.ids import ChunkId
from docparser.ir.types import NonEmptyNfcString, Sha256Digest
from docparser.retrieval.chunking import FixedChunkConfig, StructureChunkConfig
from docparser.retrieval.dense import QueryRetrieval
from docparser.retrieval.index import IndexManifest


class RetrievalGoldIndexIdentity(StrictIRModel):
    """Exact retrieval-corpus identity to which human gold labels apply."""

    index_manifest_digest: Sha256Digest
    chunker_version: NonEmptyNfcString
    chunking_policy: Literal["FIXED", "STRUCTURE"] = "FIXED"
    chunk_config: FixedChunkConfig | StructureChunkConfig
    document_revisions: Annotated[tuple[tuple[str, str], ...], Field(min_length=1)]


class RetrievalGoldQuery(StrictIRModel):
    """Human-approved supporting chunks for one frozen benchmark query."""

    query_id: NonEmptyNfcString
    acceptable_chunk_ids: Annotated[tuple[ChunkId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_unique_chunks(self) -> Self:
        if len(set(self.acceptable_chunk_ids)) != len(self.acceptable_chunk_ids):
            raise ValueError("acceptable_chunk_ids must be unique")
        return self


class RetrievalGoldManifest(StrictIRModel):
    """Independent, frozen retrieval gold bound to one exact index."""

    version: Literal["retrieval-gold@1.0.0"] = "retrieval-gold@1.0.0"
    index_identity: RetrievalGoldIndexIdentity
    queries: Annotated[tuple[RetrievalGoldQuery, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_unique_queries(self) -> Self:
        query_ids = [item.query_id for item in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("retrieval gold query_id values must be unique")
        return self


class EvidenceRankStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"


class EvidenceRankResult(StrictIRModel):
    query_id: NonEmptyNfcString
    status: EvidenceRankStatus
    rank: int | None = Field(default=None, strict=True, ge=1)
    matched_chunk_id: ChunkId | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> Self:
        has_match = self.rank is not None and self.matched_chunk_id is not None
        if (self.status is EvidenceRankStatus.HIT) != has_match:
            raise ValueError("HIT requires rank and matched_chunk_id; MISS forbids both")
        return self


class EvidenceRetrievalMetrics(StrictIRModel):
    query_count: int = Field(strict=True, ge=0)
    hit_count_at_1: int = Field(strict=True, ge=0)
    hit_count_at_5: int = Field(strict=True, ge=0)
    hit_count_at_10: int = Field(strict=True, ge=0)
    evidence_recall_at_1: float | None
    evidence_recall_at_5: float | None
    evidence_recall_at_10: float | None
    reciprocal_rank_sum: float
    mrr: float | None


class EvidenceRetrievalReport(StrictIRModel):
    evaluator_version: str = "retrieval-chunk-gold@1.0.0"
    index_identity: RetrievalGoldIndexIdentity
    results: tuple[EvidenceRankResult, ...]
    metrics: EvidenceRetrievalMetrics


def retrieval_gold_index_identity(manifest: IndexManifest) -> RetrievalGoldIndexIdentity:
    """Derive the path-independent identity used to freeze retrieval gold."""

    manifest_payload = manifest.model_dump(mode="json")
    if manifest.version.startswith("fixed-evidence-index@"):
        # Preserve the digest of already-frozen Fixed index manifests.
        manifest_payload.pop("chunking_policy", None)
    payload = json.dumps(
        manifest_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = Sha256Digest(f"sha256:{hashlib.sha256(payload).hexdigest()}")
    return RetrievalGoldIndexIdentity(
        index_manifest_digest=digest,
        chunker_version=manifest.chunker_version,
        chunking_policy=manifest.chunking_policy,
        chunk_config=manifest.chunk_config,
        document_revisions=tuple(
            (document_id, revision_id)
            for document_id, revision_id, _source_digest in manifest.documents
        ),
    )


def load_retrieval_gold_manifest(path: Path) -> RetrievalGoldManifest:
    """Load a human-authored frozen gold manifest without deriving labels."""

    return RetrievalGoldManifest.model_validate_json(path.read_bytes())


def _evidence_rank(
    gold: RetrievalGoldQuery, result: QueryRetrieval | None
) -> EvidenceRankResult:
    if result is None:
        return EvidenceRankResult(query_id=gold.query_id, status=EvidenceRankStatus.MISS)
    acceptable = frozenset(gold.acceptable_chunk_ids)
    for hit in sorted(result.hits, key=lambda item: (item.rank, str(item.chunk_id))):
        if hit.chunk_id in acceptable:
            return EvidenceRankResult(
                query_id=gold.query_id,
                status=EvidenceRankStatus.HIT,
                rank=hit.rank,
                matched_chunk_id=hit.chunk_id,
            )
    return EvidenceRankResult(query_id=gold.query_id, status=EvidenceRankStatus.MISS)


def evaluate_retrieval_gold(
    gold: RetrievalGoldManifest,
    index_manifest: IndexManifest,
    retrievals: tuple[QueryRetrieval, ...],
) -> EvidenceRetrievalReport:
    """Evaluate exact human-approved chunk IDs against final retrieval ranks."""

    current_identity = retrieval_gold_index_identity(index_manifest)
    if current_identity != gold.index_identity:
        raise ValueError("retrieval gold index identity differs from the current index")
    result_ids = [result.benchmark_query_id for result in retrievals]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("retrieval result benchmark_query_id values must be unique")
    gold_ids = {item.query_id for item in gold.queries}
    unknown = sorted(set(result_ids) - gold_ids)
    if unknown:
        raise ValueError(f"retrieval results contain unknown gold query ID: {unknown[0]}")
    by_id = {result.benchmark_query_id: result for result in retrievals}
    results = tuple(_evidence_rank(item, by_id.get(item.query_id)) for item in gold.queries)
    ranks = tuple(item.rank for item in results)
    count = len(results)
    hit_1 = sum(rank is not None and rank <= 1 for rank in ranks)
    hit_5 = sum(rank is not None and rank <= 5 for rank in ranks)
    hit_10 = sum(rank is not None and rank <= 10 for rank in ranks)
    reciprocal_rank_sum = sum(1.0 / rank for rank in ranks if rank is not None)
    return EvidenceRetrievalReport(
        index_identity=current_identity,
        results=results,
        metrics=EvidenceRetrievalMetrics(
            query_count=count,
            hit_count_at_1=hit_1,
            hit_count_at_5=hit_5,
            hit_count_at_10=hit_10,
            evidence_recall_at_1=hit_1 / count if count else None,
            evidence_recall_at_5=hit_5 / count if count else None,
            evidence_recall_at_10=hit_10 / count if count else None,
            reciprocal_rank_sum=reciprocal_rank_sum,
            mrr=reciprocal_rank_sum / count if count else None,
        ),
    )


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

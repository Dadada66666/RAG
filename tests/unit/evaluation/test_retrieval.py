from __future__ import annotations

import pytest
from tests.ir_factory import TEST_NAMESPACE

from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import (
    EvidenceRankStatus,
    RetrievalGoldManifest,
    RetrievalGoldQuery,
    RetrievalSlice,
    evaluate_page_retrieval,
    evaluate_retrieval_gold,
    evaluate_table_source_exposure,
    retrieval_gold_index_identity,
)
from docparser.ir.ids import ChunkId, generate_uuid5_id
from docparser.retrieval.chunking import FixedChunkConfig
from docparser.retrieval.dense import QueryRetrieval, RetrievedChunk
from docparser.retrieval.index import IndexManifest


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
    chunk_id: ChunkId | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id
        or generate_uuid5_id(ChunkId, TEST_NAMESPACE, query_id, str(rank)),
        document_name=document,
        rank=rank,
        score=1.0 / rank,
        page_numbers=pages,
    )


def _index_manifest(*, chunk_count: int = 3) -> IndexManifest:
    return IndexManifest(
        chunk_config=FixedChunkConfig(target_tokens=512, overlap_tokens=64),
        model_id="test/bge-m3",
        model_digest=f"sha256:{'1' * 64}",
        tokenizer_id="test-tokenizer",
        documents=(("doc-one", "rev-one", f"sha256:{'2' * 64}"),),
        chunk_count=chunk_count,
        vector_dimensions=4,
        file_digests={
            "chunks.jsonl": f"sha256:{'3' * 64}",
            "vectors.npy": f"sha256:{'4' * 64}",
        },
        warnings=(),
    )


def _chunk_id(name: str) -> ChunkId:
    return generate_uuid5_id(ChunkId, TEST_NAMESPACE, "retrieval-gold", name)


def _gold(
    manifest: IndexManifest,
    query_id: str,
    acceptable_chunk_ids: tuple[ChunkId, ...],
) -> RetrievalGoldManifest:
    return RetrievalGoldManifest(
        index_identity=retrieval_gold_index_identity(manifest),
        queries=(
            RetrievalGoldQuery(
                query_id=query_id,
                acceptable_chunk_ids=acceptable_chunk_ids,
            ),
        ),
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


def test_gold_evidence_rank_is_the_rank_of_the_first_acceptable_chunk() -> None:
    manifest = _index_manifest()
    target = _chunk_id("target")
    retrieval = QueryRetrieval(
        benchmark_query_id="query-one",
        document_name="corpus",
        hits=(
            _hit("distractor", (99,), 1),
            _hit("target", (), 3, document="other/document", chunk_id=target),
        ),
    )

    report = evaluate_retrieval_gold(
        _gold(manifest, "query-one", (target,)), manifest, (retrieval,)
    )

    assert report.results[0].status is EvidenceRankStatus.HIT
    assert report.results[0].rank == 3
    assert report.results[0].matched_chunk_id == target
    assert report.metrics.evidence_recall_at_1 == 0.0
    assert report.metrics.evidence_recall_at_5 == 1.0
    assert report.metrics.mrr == pytest.approx(1 / 3)


def test_gold_with_multiple_acceptable_chunks_uses_the_earliest_final_rank() -> None:
    manifest = _index_manifest()
    earlier = _chunk_id("earlier")
    later = _chunk_id("later")
    retrieval = QueryRetrieval(
        benchmark_query_id="query-multiple",
        document_name="corpus",
        hits=(
            _hit("later", (1,), 4, chunk_id=later),
            _hit("earlier", (1,), 2, chunk_id=earlier),
        ),
    )

    result = evaluate_retrieval_gold(
        _gold(manifest, "query-multiple", (later, earlier)), manifest, (retrieval,)
    ).results[0]

    assert result.rank == 2
    assert result.matched_chunk_id == earlier


def test_gold_evidence_rank_is_miss_without_an_exact_chunk_id_hit() -> None:
    manifest = _index_manifest()
    retrieval = QueryRetrieval(
        benchmark_query_id="query-miss",
        document_name="corpus",
        hits=(_hit("other", (1,), 1, chunk_id=_chunk_id("other")),),
    )

    report = evaluate_retrieval_gold(
        _gold(manifest, "query-miss", (_chunk_id("expected"),)), manifest, (retrieval,)
    )

    assert report.results[0].status is EvidenceRankStatus.MISS
    assert report.results[0].rank is None
    assert report.results[0].matched_chunk_id is None
    assert report.metrics.evidence_recall_at_10 == 0.0
    assert report.metrics.mrr == 0.0


def test_gold_evaluation_rejects_a_different_index_identity() -> None:
    frozen_manifest = _index_manifest(chunk_count=3)
    current_manifest = _index_manifest(chunk_count=4)
    gold = _gold(frozen_manifest, "query-identity", (_chunk_id("target"),))

    with pytest.raises(ValueError, match="index identity differs"):
        evaluate_retrieval_gold(gold, current_manifest, ())


def test_gold_evaluation_is_independent_of_chunk_text_and_page_anchors() -> None:
    manifest = _index_manifest()
    target = _chunk_id("text-independent")
    # Chunk text is intentionally not part of either the frozen gold or retrieval-hit contract.
    deliberately_misspelled_chunk_text = "Operation Zbera | Role Btea | Dneied"
    retrieval = QueryRetrieval(
        benchmark_query_id="query-text-independent",
        document_name="wrong/document",
        hits=(_hit("target", (999,), 1, document="wrong/document", chunk_id=target),),
    )

    result = evaluate_retrieval_gold(
        _gold(manifest, "query-text-independent", (target,)), manifest, (retrieval,)
    ).results[0]

    assert deliberately_misspelled_chunk_text != "Operation Zebra | Role Beta | Denied"
    assert "evidence_context" not in RetrievalGoldQuery.model_fields
    assert result.status is EvidenceRankStatus.HIT
    assert result.rank == 1


def test_gold_evaluation_does_not_fuzzy_match_similar_chunk_id_sources() -> None:
    manifest = _index_manifest()
    approved = _chunk_id("supporting-chunk")
    similar_but_distinct = _chunk_id("supporting-chunk-typo")
    retrieval = QueryRetrieval(
        benchmark_query_id="query-exact-only",
        document_name="corpus",
        hits=(_hit("similar", (1,), 1, chunk_id=similar_but_distinct),),
    )

    result = evaluate_retrieval_gold(
        _gold(manifest, "query-exact-only", (approved,)), manifest, (retrieval,)
    ).results[0]

    assert approved != similar_but_distinct
    assert result.status is EvidenceRankStatus.MISS

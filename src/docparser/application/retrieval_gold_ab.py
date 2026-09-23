"""Compare two frozen evidence indexes using the production dense and rerank path."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import (
    EvidenceRetrievalReport,
    RetrievalGoldManifest,
    evaluate_retrieval_gold,
    load_retrieval_gold_manifest,
    retrieval_gold_index_identity,
)
from docparser.retrieval.context import ContextConfig, EvidenceContext
from docparser.retrieval.dense import EmbeddingRuntime, QueryRetrieval, exact_cosine_retrieval
from docparser.retrieval.index import DenseEvidenceIndex, QASearchSession, load_evidence_index
from docparser.retrieval.rerank import RerankerRuntime, rerank_retrieval

_CANDIDATE_K = 20
_TOP_K = 5
_CONTEXT_CONFIG = ContextConfig(
    max_tokens=4096,
    table_policy="LOGICAL_ROWS",
    caption_context=True,
)
RetrievalScope = Literal["corpus", "document"]


@dataclass(frozen=True, slots=True)
class RetrievalGoldABOutcome:
    query_count: int
    comparison: dict[str, object]


def _load_queries(path: Path) -> tuple[RetrievalGroundTruth, ...]:
    queries = tuple(
        RetrievalGroundTruth.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    identifiers = [query.benchmark_query_id for query in queries]
    if not queries or len(identifiers) != len(set(identifiers)):
        raise ValueError("prepared queries must be nonempty with unique benchmark_query_id values")
    return queries


def _validate_inputs(
    fixed: DenseEvidenceIndex,
    structure: DenseEvidenceIndex,
    queries: tuple[RetrievalGroundTruth, ...],
    fixed_gold: RetrievalGoldManifest,
    structure_gold: RetrievalGoldManifest,
    scope: RetrievalScope,
    document_map: dict[str, str] | None,
) -> None:
    if (
        fixed.manifest.chunking_policy != "FIXED"
        or structure.manifest.chunking_policy != "STRUCTURE"
    ):
        raise ValueError("evidence A/B requires one Fixed and one Structure index")
    if fixed.manifest.documents != structure.manifest.documents:
        raise ValueError(
            "evidence A/B indexes contain different document revisions or source digests"
        )
    if (
        fixed.manifest.model_id,
        fixed.manifest.model_digest,
        fixed.manifest.tokenizer_id,
        fixed.manifest.vector_dimensions,
    ) != (
        structure.manifest.model_id,
        structure.manifest.model_digest,
        structure.manifest.tokenizer_id,
        structure.manifest.vector_dimensions,
    ):
        raise ValueError("evidence A/B indexes use different embedding models or tokenizers")
    if scope not in {"corpus", "document"}:
        raise ValueError("evidence A/B scope must be corpus or document")
    if scope == "document" and document_map is None:
        raise ValueError("document-scoped evidence A/B requires an explicit document map")
    planned_names = {query.document_name for query in queries}
    if document_map is not None:
        if set(document_map) != planned_names:
            raise ValueError("document map names differ from the complete planned query documents")
        if len(set(document_map.values())) != len(document_map):
            raise ValueError("document map must identify distinct indexed documents")
        index_ids = {document_id for document_id, _, _ in fixed.manifest.documents}
        if set(document_map.values()) - index_ids:
            raise ValueError("document map contains an ID absent from the compared indexes")

    planned = {query.benchmark_query_id for query in queries}
    for name, index, gold in (
        ("Fixed", fixed, fixed_gold),
        ("Structure", structure, structure_gold),
    ):
        if gold.index_identity != retrieval_gold_index_identity(index.manifest):
            raise ValueError(f"{name} gold index identity differs from the supplied index")
        if {item.query_id for item in gold.queries} != planned:
            raise ValueError(f"{name} gold query IDs differ from the complete planned query set")
        if document_map is not None and set(document_map.values()) - {
            str(entry.chunk.document_id) for entry in index.entries
        }:
            raise ValueError(f"{name} index has no retrieval chunks for a mapped document")
        chunk_names = {
            entry.chunk.chunk_id: str(entry.chunk.document_id) for entry in index.entries
        }
        query_names = {query.benchmark_query_id: query.document_name for query in queries}
        for item in gold.queries:
            for chunk_id in item.acceptable_chunk_ids:
                if chunk_id not in chunk_names:
                    raise ValueError(
                        f"{name} gold contains a chunk absent from its index: {chunk_id}"
                    )
                if document_map is not None and chunk_names[chunk_id] != document_map[
                    query_names[item.query_id]
                ]:
                    raise ValueError(f"{name} gold chunk belongs to a different query document")


def _dense_results(
    session: QASearchSession,
    queries: tuple[RetrievalGroundTruth, ...],
    query_vectors: object,
    scope: RetrievalScope,
    document_map: dict[str, str] | None,
) -> tuple[QueryRetrieval, ...]:
    query_ids = tuple(query.benchmark_query_id for query in queries)
    query_names = tuple(query.document_name for query in queries)
    if scope == "corpus":
        return exact_cosine_retrieval(
            query_ids=query_ids,
            query_document_names=query_names,
            query_vectors=query_vectors,
            chunks=session.chunks,
            chunk_document_names=session.names,
            chunk_vectors=session.index.vectors,
            top_k=_CANDIDATE_K,
        )

    from docparser.retrieval.dense import _numpy

    np = _numpy()
    vectors = np.asarray(query_vectors, dtype=np.float32)
    assert document_map is not None
    rows_by_name: dict[str, tuple[int, ...]] = {
        name: tuple(
            row
            for row, chunk in enumerate(session.chunks)
            if str(chunk.document_id) == document_map[name]
        )
        for name in set(query_names)
    }
    results: list[QueryRetrieval] = []
    for row, query in enumerate(queries):
        selected = rows_by_name[query.document_name]
        results.extend(
            exact_cosine_retrieval(
                query_ids=(query.benchmark_query_id,),
                query_document_names=(query.document_name,),
                query_vectors=vectors[row : row + 1],
                chunks=tuple(session.chunks[index] for index in selected),
                chunk_document_names=tuple(session.names[index] for index in selected),
                chunk_vectors=session.index.vectors[list(selected)],
                top_k=_CANDIDATE_K,
            )
        )
    return tuple(results)


def _rerank_results(
    session: QASearchSession,
    queries: tuple[RetrievalGroundTruth, ...],
    candidates: tuple[QueryRetrieval, ...],
    reranker: RerankerRuntime,
) -> tuple[QueryRetrieval, ...]:
    texts = {str(chunk.chunk_id): chunk.text for chunk in session.chunks}
    return tuple(
        rerank_retrieval(
            question=query.question,
            candidates=candidate,
            chunk_text_by_id=texts,
            runtime=reranker,
            top_k=_TOP_K,
        )
        for query, candidate in zip(queries, candidates, strict=True)
    )


def _rank_metrics(ranks: Sequence[int | None], *, dense: bool) -> dict[str, object]:
    count = len(ranks)
    hit_1 = sum(rank is not None and rank <= 1 for rank in ranks)
    hit_5 = sum(rank is not None and rank <= 5 for rank in ranks)
    result: dict[str, object] = {
        "query_count": count,
        "hit_count_at_1": hit_1,
        "hit_count_at_5": hit_5,
        "evidence_recall_at_1": hit_1 / count if count else None,
        "evidence_recall_at_5": hit_5 / count if count else None,
        "mrr": sum(1.0 / rank for rank in ranks if rank is not None) / count if count else None,
    }
    if dense:
        hits = sum(rank is not None and rank <= _CANDIDATE_K for rank in ranks)
        result["hit_count_at_20"] = hits
        result["evidence_recall_at_20"] = hits / count if count else None
    return result


def _metrics(
    report: EvidenceRetrievalReport,
    queries: tuple[RetrievalGroundTruth, ...],
    *,
    dense: bool,
) -> dict[str, object]:
    ranks = {item.query_id: item.rank for item in report.results}
    values = report.metrics
    result: dict[str, object] = {
        "query_count": values.query_count,
        "hit_count_at_1": values.hit_count_at_1,
        "hit_count_at_5": values.hit_count_at_5,
        "evidence_recall_at_1": values.evidence_recall_at_1,
        "evidence_recall_at_5": values.evidence_recall_at_5,
        "mrr": values.mrr,
    }
    if dense:
        hits = sum(item.rank is not None and item.rank <= _CANDIDATE_K for item in report.results)
        result["hit_count_at_20"] = hits
        result["evidence_recall_at_20"] = hits / values.query_count
    result["by_evidence_type"] = {
        evidence_type.value: _rank_metrics(
            tuple(
                ranks[query.benchmark_query_id]
                for query in queries
                if query.evidence_type is evidence_type
            ),
            dense=dense,
        )
        for evidence_type in RetrievalEvidenceType
    }
    return result


def _metric_deltas(
    fixed: dict[str, object], structure: dict[str, object], *, dense: bool
) -> dict[str, float | None]:
    names = ["evidence_recall_at_1", "evidence_recall_at_5", "mrr"]
    if dense:
        names.append("evidence_recall_at_20")
    deltas: dict[str, float | None] = {}
    for name in names:
        baseline, candidate = fixed.get(name), structure.get(name)
        deltas[name] = (
            float(candidate) - float(baseline)
            if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float))
            else None
        )
    return deltas


def _context_gold_span_overlap(
    dense_report: EvidenceRetrievalReport,
    final_report: EvidenceRetrievalReport,
    retrievals: tuple[QueryRetrieval, ...],
    contexts: tuple[EvidenceContext, ...],
    session: QASearchSession,
) -> tuple[dict[str, object], ...]:
    """Screen for source-span overlap; this does not judge evidence sufficiency."""
    dense_by_id = {item.query_id: item for item in dense_report.results}
    final_by_id = {item.query_id: item for item in final_report.results}
    observations: list[dict[str, object]] = []
    for retrieval, context in zip(retrievals, contexts, strict=True):
        query_id = retrieval.benchmark_query_id
        dense = dense_by_id[query_id]
        result = final_by_id[query_id]
        spans = session.spans[str(result.matched_chunk_id)] if result.matched_chunk_id else ()
        observations.append(
            {
                "query_id": query_id,
                "dense_evidence_rank": dense.rank,
                "reranked_evidence_rank": result.rank,
                "dense_candidate_miss_at_20": dense.rank is None,
                "reranker_dropped_candidate": dense.rank is not None and result.rank is None,
                "gold_span_overlap_in_context": any(
                    evidence.source_id == span.source_id
                    and evidence.token_start < span.token_end
                    and span.token_start < evidence.token_end
                    for evidence in context.evidence
                    for span in spans
                ),
                "context_token_count": context.token_count,
                "context_warnings": list(context.warnings),
                "omitted_source_ids": list(context.omitted_source_ids),
                "evidence_sufficiency": "REQUIRES_INDEPENDENT_REVIEW",
            }
        )
    return tuple(observations)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Sequence[object]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def run_retrieval_gold_ab(
    *,
    fixed_index_path: Path,
    structure_index_path: Path,
    queries_path: Path,
    fixed_gold_path: Path,
    structure_gold_path: Path,
    output_dir: Path,
    runtime: EmbeddingRuntime,
    reranker: RerankerRuntime,
    scope: RetrievalScope = "corpus",
    document_map_path: Path | None = None,
    source_commit: str | None = None,
) -> RetrievalGoldABOutcome:
    """Run the same planned questions against two frozen indexes without invoking generation."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("evidence A/B output directory is not empty")
    fixed = load_evidence_index(fixed_index_path)
    structure = load_evidence_index(structure_index_path)
    queries = _load_queries(queries_path)
    document_map: dict[str, str] | None = None
    if document_map_path is not None:
        payload = json.loads(document_map_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or any(
            not isinstance(name, str) or not isinstance(identifier, str)
            for name, identifier in payload.items()
        ):
            raise ValueError("document map must be a JSON object of document names to IDs")
        document_map = payload
    fixed_gold = load_retrieval_gold_manifest(fixed_gold_path)
    structure_gold = load_retrieval_gold_manifest(structure_gold_path)
    _validate_inputs(fixed, structure, queries, fixed_gold, structure_gold, scope, document_map)

    fixed_session = fixed.session(runtime)
    structure_session = structure.session(runtime)
    query_vectors = runtime.embed(tuple(query.question for query in queries))
    fixed_dense = _dense_results(fixed_session, queries, query_vectors, scope, document_map)
    structure_dense = _dense_results(structure_session, queries, query_vectors, scope, document_map)
    fixed_final = _rerank_results(fixed_session, queries, fixed_dense, reranker)
    structure_final = _rerank_results(structure_session, queries, structure_dense, reranker)
    fixed_dense_report = evaluate_retrieval_gold(fixed_gold, fixed.manifest, fixed_dense)
    structure_dense_report = evaluate_retrieval_gold(
        structure_gold, structure.manifest, structure_dense
    )
    fixed_final_report = evaluate_retrieval_gold(fixed_gold, fixed.manifest, fixed_final)
    structure_final_report = evaluate_retrieval_gold(
        structure_gold, structure.manifest, structure_final
    )
    fixed_contexts = tuple(
        fixed_session.context(result, _CONTEXT_CONFIG) for result in fixed_final
    )
    structure_contexts = tuple(
        structure_session.context(result, _CONTEXT_CONFIG) for result in structure_final
    )

    fixed_dense_metrics = _metrics(fixed_dense_report, queries, dense=True)
    structure_dense_metrics = _metrics(structure_dense_report, queries, dense=True)
    fixed_final_metrics = _metrics(fixed_final_report, queries, dense=False)
    structure_final_metrics = _metrics(structure_final_report, queries, dense=False)
    comparison: dict[str, object] = {
        "query_count": len(queries),
        "scope": scope,
        "fixed": {
            "dense": fixed_dense_metrics,
            "reranked": fixed_final_metrics,
        },
        "structure": {
            "dense": structure_dense_metrics,
            "reranked": structure_final_metrics,
        },
        "structure_minus_fixed": {
            "dense": _metric_deltas(fixed_dense_metrics, structure_dense_metrics, dense=True),
            "reranked": _metric_deltas(fixed_final_metrics, structure_final_metrics, dense=False),
        },
    }
    observations_by_policy = {
        "fixed": _context_gold_span_overlap(
            fixed_dense_report, fixed_final_report, fixed_final, fixed_contexts, fixed_session
        ),
        "structure": _context_gold_span_overlap(
            structure_dense_report,
            structure_final_report,
            structure_final,
            structure_contexts,
            structure_session,
        ),
    }
    manifest = {
        "version": "retrieval-gold-ab@1.0.0",
        "scope": scope,
        "candidate_k": _CANDIDATE_K,
        "top_k": _TOP_K,
        "context_config": _CONTEXT_CONFIG.model_dump(mode="json"),
        "queries_sha256": "sha256:" + hashlib.sha256(queries_path.read_bytes()).hexdigest(),
        "fixed_gold_sha256": "sha256:"
        + hashlib.sha256(fixed_gold_path.read_bytes()).hexdigest(),
        "structure_gold_sha256": "sha256:"
        + hashlib.sha256(structure_gold_path.read_bytes()).hexdigest(),
        "query_ids": [query.benchmark_query_id for query in queries],
        "documents": fixed.manifest.documents,
        "document_map": document_map,
        "source_commit": source_commit,
        "gold_evaluator_version": fixed_final_report.evaluator_version,
        "embedding_model_id": runtime.model_id,
        "embedding_model_digest": str(runtime.model_digest),
        "reranker_model_id": reranker.model_id,
        "reranker_model_digest": reranker.model_digest,
        "fixed_index_identity": fixed_gold.index_identity.model_dump(mode="json"),
        "structure_index_identity": structure_gold.index_identity.model_dump(mode="json"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "run.json", manifest)
    for name, dense, final, dense_metrics, final_metrics, contexts in (
        (
            "fixed",
            fixed_dense,
            fixed_final,
            fixed_dense_metrics,
            fixed_final_metrics,
            fixed_contexts,
        ),
        (
            "structure",
            structure_dense,
            structure_final,
            structure_dense_metrics,
            structure_final_metrics,
            structure_contexts,
        ),
    ):
        directory = output_dir / name
        directory.mkdir()
        _write_jsonl(directory / "dense.jsonl", [value.model_dump(mode="json") for value in dense])
        _write_jsonl(
            directory / "reranked.jsonl", [value.model_dump(mode="json") for value in final]
        )
        _write_json(directory / "dense.metrics.json", dense_metrics)
        _write_json(directory / "reranked.metrics.json", final_metrics)
        _write_jsonl(
            directory / "context.jsonl", [value.model_dump(mode="json") for value in contexts]
        )
        _write_jsonl(
            directory / "per-query.jsonl",
            observations_by_policy[name],
        )
    _write_json(output_dir / "comparison.json", comparison)
    _write_jsonl(
        output_dir / "paired.jsonl",
        tuple(
            {
                "query_id": query.benchmark_query_id,
                "document_name": query.document_name,
                "evidence_type": query.evidence_type.value,
                "fixed": fixed_observation,
                "structure": structure_observation,
            }
            for query, fixed_observation, structure_observation in zip(
                queries,
                observations_by_policy["fixed"],
                observations_by_policy["structure"],
                strict=True,
            )
        ),
    )
    return RetrievalGoldABOutcome(query_count=len(queries), comparison=comparison)

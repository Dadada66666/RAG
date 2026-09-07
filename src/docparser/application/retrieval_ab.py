"""One-command fixed-token versus structure-aware retrieval experiment."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from docparser.evaluation.ohr import RetrievalGroundTruth
from docparser.evaluation.retrieval import (
    PageRetrievalReport,
    RetrievalSlice,
    evaluate_page_retrieval,
)
from docparser.ir.chunks import Chunk
from docparser.ir.models import DocumentIR
from docparser.ir.serialization import load_canonical_json, semantic_digest
from docparser.retrieval.chunking import (
    FixedChunkConfig,
    StructureChunkConfig,
    fixed_token_chunks,
    structure_aware_chunks,
)
from docparser.retrieval.dense import (
    BgeM3Runtime,
    EmbeddingRuntime,
    QueryRetrieval,
    RetrievalRuntimeError,
    exact_cosine_retrieval,
)


@dataclass(frozen=True, slots=True)
class RetrievalABOutcome:
    eligible_queries: tuple[RetrievalGroundTruth, ...]
    excluded_query_ids: tuple[str, ...]
    fixed_chunks: tuple[Chunk, ...]
    structure_chunks: tuple[Chunk, ...]
    fixed_retrievals: tuple[QueryRetrieval, ...]
    structure_retrievals: tuple[QueryRetrieval, ...]
    fixed_metrics: PageRetrievalReport
    structure_metrics: PageRetrievalReport
    comparison: dict[str, object]
    manifest: dict[str, object]


def _load_queries(path: Path) -> tuple[RetrievalGroundTruth, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"prepared OHR query file not found: {path}")
    queries = tuple(
        RetrievalGroundTruth.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    identifiers = [query.benchmark_query_id for query in queries]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("prepared OHR query file contains duplicate benchmark_query_id values")
    return queries


def _load_documents(ir_root: Path) -> dict[str, DocumentIR]:
    if not ir_root.is_dir():
        raise FileNotFoundError(f"Canonical IR root not found: {ir_root}")
    documents: dict[str, DocumentIR] = {}
    for path in sorted(ir_root.rglob("document.ir.json")):
        document_name = path.parent.relative_to(ir_root).as_posix()
        if document_name in documents:
            raise ValueError(f"duplicate Canonical IR document path identity: {document_name}")
        documents[document_name] = load_canonical_json(path.read_bytes())
    if not documents:
        raise FileNotFoundError(f"no document.ir.json files found below: {ir_root}")
    return documents


def _json_bytes(value: object, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":") if indent is None else None,
            indent=indent,
        )
        + "\n"
    ).encode("utf-8")


def _write_jsonl(path: Path, values: tuple[object, ...]) -> None:
    lines = [
        json.dumps(
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for value in values
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _metric_delta(
    fixed: PageRetrievalReport, structure: PageRetrievalReport
) -> dict[str, object]:
    result: dict[str, object] = {}
    for slice_name in RetrievalSlice:
        left = fixed.metrics_by_slice[slice_name]
        right = structure.metrics_by_slice[slice_name]
        result[slice_name.value] = {
            "query_count": left.query_count,
            "page_hit_rate_at_1": (
                None
                if left.page_hit_rate_at_1 is None or right.page_hit_rate_at_1 is None
                else right.page_hit_rate_at_1 - left.page_hit_rate_at_1
            ),
            "page_hit_rate_at_5": (
                None
                if left.page_hit_rate_at_5 is None or right.page_hit_rate_at_5 is None
                else right.page_hit_rate_at_5 - left.page_hit_rate_at_5
            ),
            "page_hit_rate_at_10": (
                None
                if left.page_hit_rate_at_10 is None or right.page_hit_rate_at_10 is None
                else right.page_hit_rate_at_10 - left.page_hit_rate_at_10
            ),
            "mrr": None if left.mrr is None or right.mrr is None else right.mrr - left.mrr,
        }
    return result


def _comparison(
    fixed: PageRetrievalReport, structure: PageRetrievalReport
) -> dict[str, object]:
    return {
        "metric_contract": "OHR PageHitRate@K and MRR; PageHitRate is not Recall",
        "fixed": fixed.model_dump(mode="json"),
        "structure": structure.model_dump(mode="json"),
        "delta_structure_minus_fixed": _metric_delta(fixed, structure),
    }


def _metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.6f}"


def _markdown_report(outcome: RetrievalABOutcome) -> str:
    manifest = outcome.manifest
    lines = [
        "# Fixed-token vs Structure-aware Retrieval",
        "",
        f"- Documents: {manifest['document_count']}",
        f"- Eligible queries: {manifest['eligible_query_count']}",
        f"- Excluded missing-document queries: {manifest['excluded_query_count']}",
        f"- Embedding model: {manifest['embedding_model_id']}",
        f"- Tokenizer: {manifest['tokenizer_id']}",
        f"- Fixed retrieval chunks: {manifest['fixed_embedding_chunk_count']}",
        f"- Structure retrieval chunks: {manifest['structure_embedding_chunk_count']}",
        f"- Fixed config: `{json.dumps(manifest['fixed_config'], sort_keys=True)}`",
        f"- Structure config: `{json.dumps(manifest['structure_config'], sort_keys=True)}`",
        "",
        "| Slice | System | Queries | PageHitRate@1 | PageHitRate@5 | "
        "PageHitRate@10 | MRR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name in RetrievalSlice:
        fixed = outcome.fixed_metrics.metrics_by_slice[slice_name]
        structure = outcome.structure_metrics.metrics_by_slice[slice_name]
        delta = outcome.comparison["delta_structure_minus_fixed"]
        assert isinstance(delta, dict)
        slice_delta = delta[slice_name.value]
        assert isinstance(slice_delta, dict)
        lines.extend(
            [
                f"| {slice_name.value} | Fixed | {fixed.query_count} | "
                f"{_metric(fixed.page_hit_rate_at_1)} | {_metric(fixed.page_hit_rate_at_5)} | "
                f"{_metric(fixed.page_hit_rate_at_10)} | {_metric(fixed.mrr)} |",
                f"| {slice_name.value} | Structure | {structure.query_count} | "
                f"{_metric(structure.page_hit_rate_at_1)} | "
                f"{_metric(structure.page_hit_rate_at_5)} | "
                f"{_metric(structure.page_hit_rate_at_10)} | {_metric(structure.mrr)} |",
                f"| {slice_name.value} | Delta | {fixed.query_count} | "
                f"{_metric(slice_delta['page_hit_rate_at_1'])} | "
                f"{_metric(slice_delta['page_hit_rate_at_5'])} | "
                f"{_metric(slice_delta['page_hit_rate_at_10'])} | "
                f"{_metric(slice_delta['mrr'])} |",
            ]
        )
    return "\n".join(lines) + "\n"


def run_retrieval_ab(
    *,
    ir_root: Path,
    queries_path: Path,
    output_dir: Path,
    model_path: Path | None,
    fixed_config: FixedChunkConfig,
    structure_config: StructureChunkConfig,
    top_k: int = 10,
    device: str = "cpu",
    batch_size: int = 16,
    source_commit: str | None = None,
    embedding_runtime: EmbeddingRuntime | None = None,
) -> RetrievalABOutcome:
    """Run both retrieval systems with one query embedding matrix and one evaluator."""

    if top_k < 10:
        raise ValueError("A/B evaluation requires top_k >= 10 for PageHitRate@10")
    documents = _load_documents(ir_root)
    all_queries = _load_queries(queries_path)
    eligible = tuple(query for query in all_queries if query.document_name in documents)
    excluded = tuple(
        query.benchmark_query_id for query in all_queries if query.document_name not in documents
    )
    if not eligible:
        raise ValueError("no prepared OHR queries resolve to a Canonical IR document")
    runtime = embedding_runtime
    if runtime is None:
        if model_path is None:
            raise ValueError("model_path is required when no embedding runtime is supplied")
        runtime = BgeM3Runtime(model_path, device=device, batch_size=batch_size)
    tokenizer = runtime.tokenizer

    relevant_names = tuple(sorted({query.document_name for query in eligible}))
    fixed_all_pairs: list[tuple[str, Chunk]] = []
    structure_all_pairs: list[tuple[str, Chunk]] = []
    for document_name in relevant_names:
        document = documents[document_name]
        fixed_all_pairs.extend(
            (document_name, chunk)
            for chunk in fixed_token_chunks(document, tokenizer, fixed_config)
        )
        structure_all_pairs.extend(
            (document_name, chunk)
            for chunk in structure_aware_chunks(document, tokenizer, structure_config)
        )
    fixed_pairs = [pair for pair in fixed_all_pairs if pair[1].embedding_eligible]
    structure_pairs = [pair for pair in structure_all_pairs if pair[1].embedding_eligible]
    if not fixed_pairs or not structure_pairs:
        raise ValueError("both chunking systems must produce embedding-eligible chunks")

    query_vectors = runtime.embed([query.question for query in eligible])
    fixed_vectors = runtime.embed([chunk.text for _, chunk in fixed_pairs])
    structure_vectors = runtime.embed([chunk.text for _, chunk in structure_pairs])
    query_ids = [query.benchmark_query_id for query in eligible]
    query_documents = [query.document_name for query in eligible]
    fixed_retrievals = exact_cosine_retrieval(
        query_ids=query_ids,
        query_document_names=query_documents,
        query_vectors=query_vectors,
        chunks=[chunk for _, chunk in fixed_pairs],
        chunk_document_names=[name for name, _ in fixed_pairs],
        chunk_vectors=fixed_vectors,
        top_k=top_k,
    )
    structure_retrievals = exact_cosine_retrieval(
        query_ids=query_ids,
        query_document_names=query_documents,
        query_vectors=query_vectors,
        chunks=[chunk for _, chunk in structure_pairs],
        chunk_document_names=[name for name, _ in structure_pairs],
        chunk_vectors=structure_vectors,
        top_k=top_k,
    )
    fixed_metrics = evaluate_page_retrieval(eligible, fixed_retrievals)
    structure_metrics = evaluate_page_retrieval(eligible, structure_retrievals)
    comparison = _comparison(fixed_metrics, structure_metrics)
    manifest: dict[str, object] = {
        "experiment_version": "rag-retrieval-ab@1.0.0",
        "source_commit": source_commit,
        "document_count": len(relevant_names),
        "documents": [
            {
                "document_name": name,
                "document_id": str(documents[name].document_id),
                "revision_id": str(documents[name].revision_id),
                "semantic_digest": str(semantic_digest(documents[name])),
            }
            for name in relevant_names
        ],
        "eligible_query_count": len(eligible),
        "eligible_query_ids": query_ids,
        "eligible_query_count_by_evidence_type": dict(
            sorted(Counter(query.evidence_type.value for query in eligible).items())
        ),
        "eligible_query_digest": "sha256:"
        + hashlib.sha256(
            _json_bytes([query.model_dump(mode="json") for query in eligible]).rstrip(b"\n")
        ).hexdigest(),
        "excluded_query_count": len(excluded),
        "excluded_query_ids": list(excluded),
        "excluded_queries": [
            {
                "benchmark_query_id": query.benchmark_query_id,
                "document_name": query.document_name,
                "reason": "CANONICAL_IR_DOCUMENT_MISSING",
            }
            for query in all_queries
            if query.document_name not in documents
        ],
        "fixed_config": fixed_config.model_dump(mode="json"),
        "structure_config": structure_config.model_dump(mode="json"),
        "fixed_chunker_version": fixed_all_pairs[0][1].chunker_version,
        "structure_chunker_version": structure_all_pairs[0][1].chunker_version,
        "fixed_embedding_chunk_ids": [str(chunk.chunk_id) for _, chunk in fixed_pairs],
        "structure_embedding_chunk_ids": [str(chunk.chunk_id) for _, chunk in structure_pairs],
        "fixed_embedding_chunk_count": len(fixed_pairs),
        "structure_embedding_chunk_count": len(structure_pairs),
        "tokenizer_id": tokenizer.tokenizer_id,
        "embedding_model_id": runtime.model_id,
        "embedding_model_digest": str(runtime.model_digest),
        "embedding_device": device,
        "embedding_batch_size": batch_size,
        "embedding_dimension": int(query_vectors.shape[1]),
        "embedding_dtype": str(query_vectors.dtype),
        "l2_normalized": True,
        "retriever": "numpy-exact-cosine@1.0.0",
        "top_k": top_k,
    }
    outcome = RetrievalABOutcome(
        eligible_queries=eligible,
        excluded_query_ids=excluded,
        fixed_chunks=tuple(chunk for _, chunk in fixed_all_pairs),
        structure_chunks=tuple(chunk for _, chunk in structure_all_pairs),
        fixed_retrievals=fixed_retrievals,
        structure_retrievals=structure_retrievals,
        fixed_metrics=fixed_metrics,
        structure_metrics=structure_metrics,
        comparison=comparison,
        manifest=manifest,
    )

    try:
        import numpy as np
    except ImportError as exc:
        raise RetrievalRuntimeError(
            "NumPy is required to write retrieval embeddings; "
            "install enterprise-docparser[retrieval]"
        ) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("fixed", "structure"):
        (output_dir / name).mkdir(exist_ok=True)
    (output_dir / "manifest.json").write_bytes(_json_bytes(manifest, indent=2))
    _write_jsonl(output_dir / "queries.jsonl", eligible)
    _write_jsonl(output_dir / "fixed" / "chunks.jsonl", outcome.fixed_chunks)
    np.save(output_dir / "fixed" / "embeddings.npy", fixed_vectors, allow_pickle=False)
    _write_jsonl(output_dir / "fixed" / "retrieval.jsonl", fixed_retrievals)
    (output_dir / "fixed" / "metrics.json").write_bytes(
        _json_bytes(fixed_metrics.model_dump(mode="json"), indent=2)
    )
    _write_jsonl(output_dir / "structure" / "chunks.jsonl", outcome.structure_chunks)
    np.save(output_dir / "structure" / "embeddings.npy", structure_vectors, allow_pickle=False)
    _write_jsonl(output_dir / "structure" / "retrieval.jsonl", structure_retrievals)
    (output_dir / "structure" / "metrics.json").write_bytes(
        _json_bytes(structure_metrics.model_dump(mode="json"), indent=2)
    )
    (output_dir / "comparison.json").write_bytes(_json_bytes(comparison, indent=2))
    (output_dir / "report.md").write_text(_markdown_report(outcome), encoding="utf-8")
    return outcome

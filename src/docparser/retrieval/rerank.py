"""Optional local BGE reranking over an existing dense candidate pool."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from docparser.retrieval.dense import QueryRetrieval, RetrievalRuntimeError


class RerankerRuntime(Protocol):
    """Score query/passage pairs without changing candidate identity or content."""

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]: ...


class BgeRerankerV2M3Runtime:
    """Lazy local-only runtime for BAAI/bge-reranker-v2-m3."""

    def __init__(self, model_path: Path, *, device: str = "cpu", batch_size: int = 16) -> None:
        if not model_path.is_dir():
            raise RetrievalRuntimeError(f"local BGE reranker directory not found: {model_path}")
        if batch_size < 1:
            raise ValueError("reranker batch_size must be >= 1")
        self._model_path = model_path
        self._device = device
        self._batch_size = batch_size
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RetrievalRuntimeError(
                    "sentence-transformers is required for BGE reranking; "
                    "install enterprise-docparser[retrieval]"
                ) from exc
            self._model = CrossEncoder(
                str(self._model_path),
                device=self._device,
                local_files_only=True,
            )
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        if not passages:
            return ()
        raw = self._load_model().predict(
            [(query, passage) for passage in passages],
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        from docparser.retrieval.dense import _numpy

        np = _numpy()
        values = np.asarray(raw, dtype=np.float32)
        if values.ndim == 2 and values.shape[1] == 1:
            values = values[:, 0]
        if values.ndim != 1 or values.shape[0] != len(passages):
            raise RetrievalRuntimeError("BGE reranker returned an invalid score vector")
        if not bool(np.all(np.isfinite(values))):
            raise RetrievalRuntimeError("BGE reranker returned a non-finite relevance score")
        return tuple(float(value) for value in values)


def rerank_retrieval(
    *,
    question: str,
    candidates: QueryRetrieval,
    chunk_text_by_id: Mapping[str, str],
    runtime: RerankerRuntime,
    top_k: int,
) -> QueryRetrieval:
    """Rerank only the supplied dense candidates with stable provenance and ties."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    passages = tuple(chunk_text_by_id[str(hit.chunk_id)] for hit in candidates.hits)
    scores = runtime.score(question, passages)
    if len(scores) != len(candidates.hits):
        raise RetrievalRuntimeError("reranker score count differs from dense candidate count")
    scored = tuple(zip(candidates.hits, scores, strict=True))
    ordered = sorted(
        scored,
        key=lambda item: (-item[1], item[0].rank, str(item[0].chunk_id)),
    )[:top_k]
    return candidates.model_copy(
        update={
            "hits": tuple(
                hit.model_copy(
                    update={
                        "rank": rank,
                        "score": score,
                        "dense_rank": hit.rank,
                        "dense_score": hit.score,
                        "reranker_score": score,
                    }
                )
                for rank, (hit, score) in enumerate(ordered, start=1)
            )
        }
    )

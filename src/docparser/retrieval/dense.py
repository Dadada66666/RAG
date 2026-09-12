"""Local BGE-M3 dense embeddings and exact NumPy cosine retrieval."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from docparser.ir.base import StrictIRModel
from docparser.ir.chunks import Chunk
from docparser.ir.ids import ChunkId
from docparser.ir.types import NonEmptyNfcString, Sha256Digest
from docparser.retrieval.chunking import Tokenizer
from docparser.retrieval.table_context import SourceEncoding


class RetrievalRuntimeError(RuntimeError):
    """Raised for missing or invalid local dense-retrieval runtime state."""


class EmbeddingRuntime(Protocol):
    @property
    def tokenizer(self) -> Tokenizer: ...

    @property
    def model_id(self) -> str: ...

    @property
    def model_digest(self) -> Sha256Digest: ...

    def embed(self, texts: Sequence[str]) -> Any: ...


class RetrievedChunk(StrictIRModel):
    chunk_id: ChunkId
    document_name: NonEmptyNfcString
    rank: int = Field(strict=True, ge=1)
    score: float
    page_numbers: tuple[int, ...]


class QueryRetrieval(StrictIRModel):
    benchmark_query_id: NonEmptyNfcString
    document_name: NonEmptyNfcString
    hits: tuple[RetrievedChunk, ...]


class _SentenceTransformerTokenizer:
    def __init__(self, tokenizer: Any, tokenizer_id: str) -> None:
        self._tokenizer = tokenizer
        self._tokenizer_id = tokenizer_id

    @property
    def tokenizer_id(self) -> str:
        return self._tokenizer_id

    def encode(self, text: str) -> tuple[int, ...]:
        values = self._tokenizer.encode(text, add_special_tokens=False)
        return tuple(int(value) for value in values)

    def decode(self, token_ids: Sequence[int]) -> str:
        return str(
            self._tokenizer.decode(
                list(token_ids),
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
        )

    def encode_with_offsets(self, text: str) -> SourceEncoding | None:
        if not getattr(self._tokenizer, "is_fast", False):
            return None
        encoded = self._tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_offsets_mapping=True,
            return_attention_mask=False,
        )
        return SourceEncoding(
            tuple(int(value) for value in encoded["input_ids"]),
            tuple((int(start), int(end)) for start, end in encoded["offset_mapping"]),
        )


def _local_directory_digest(root: Path) -> Sha256Digest:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise RetrievalRuntimeError(f"BGE-M3 model directory is empty: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            while data := handle.read(1024 * 1024):
                digest.update(data)
    return Sha256Digest(f"sha256:{digest.hexdigest()}")


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise RetrievalRuntimeError(
            "NumPy is required for dense retrieval; install enterprise-docparser[retrieval]"
        ) from exc
    return numpy


class BgeM3Runtime:
    """Lazy, local-only SentenceTransformers runtime for BAAI/bge-m3 dense vectors."""

    def __init__(self, model_path: Path, *, device: str = "cpu", batch_size: int = 16) -> None:
        if not model_path.is_dir():
            raise RetrievalRuntimeError(f"local BGE-M3 model directory not found: {model_path}")
        if batch_size < 1:
            raise ValueError("embedding batch_size must be >= 1")
        self._model_path = model_path
        self._device = device
        self._batch_size = batch_size
        self._model: Any | None = None
        self._tokenizer: Tokenizer | None = None
        self._model_digest: Sha256Digest | None = None

    @property
    def model_id(self) -> str:
        return f"BAAI/bge-m3:{self._model_path.name}"

    @property
    def model_digest(self) -> Sha256Digest:
        if self._model_digest is None:
            self._model_digest = _local_directory_digest(self._model_path)
        return self._model_digest

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RetrievalRuntimeError(
                    "sentence-transformers is required for BGE-M3; "
                    "install enterprise-docparser[retrieval]"
                ) from exc
            self._model = SentenceTransformer(
                str(self._model_path),
                device=self._device,
                local_files_only=True,
            )
        return self._model

    @property
    def tokenizer(self) -> Tokenizer:
        if self._tokenizer is None:
            model = self._load_model()
            self._tokenizer = _SentenceTransformerTokenizer(
                model.tokenizer,
                f"{self.model_id}@{self.model_digest}",
            )
        return self._tokenizer

    def embed(self, texts: Sequence[str]) -> Any:
        np = _numpy()
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        raw = self._load_model().encode(
            list(texts),
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        vectors = np.asarray(raw, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            raise RetrievalRuntimeError("BGE-M3 returned an invalid dense embedding matrix")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if not bool(np.all(np.isfinite(vectors))) or bool(np.any(norms == 0.0)):
            raise RetrievalRuntimeError("BGE-M3 returned an invalid dense embedding")
        return np.asarray(vectors / norms, dtype=np.float32)


def exact_cosine_retrieval(
    *,
    query_ids: Sequence[str],
    query_document_names: Sequence[str],
    query_vectors: Any,
    chunks: Sequence[Chunk],
    chunk_document_names: Sequence[str],
    chunk_vectors: Any,
    top_k: int,
) -> tuple[QueryRetrieval, ...]:
    """Return stable exact-cosine ranks over already L2-normalized dense vectors."""

    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if len(query_ids) != len(query_document_names):
        raise ValueError("query IDs and document names must have equal length")
    if len(chunks) != len(chunk_document_names):
        raise ValueError("chunks and document names must have equal length")
    np = _numpy()
    queries = np.asarray(query_vectors, dtype=np.float32)
    corpus = np.asarray(chunk_vectors, dtype=np.float32)
    if queries.ndim != 2 or corpus.ndim != 2:
        raise ValueError("query and chunk embeddings must be rank-2 matrices")
    if queries.shape[0] != len(query_ids) or corpus.shape[0] != len(chunks):
        raise ValueError("embedding matrix rows must match query and chunk counts")
    if queries.shape[1] != corpus.shape[1]:
        raise ValueError("query and chunk embedding dimensions must match")
    query_norms = np.linalg.norm(queries, axis=1)
    corpus_norms = np.linalg.norm(corpus, axis=1)
    if not bool(np.allclose(query_norms, 1.0, rtol=1e-5, atol=1e-6)) or not bool(
        np.allclose(corpus_norms, 1.0, rtol=1e-5, atol=1e-6)
    ):
        raise ValueError("exact cosine retrieval requires L2-normalized vectors")
    scores = queries @ corpus.T
    limit = min(top_k, len(chunks))
    results: list[QueryRetrieval] = []
    for query_index, query_id in enumerate(query_ids):
        ordered = sorted(
            range(len(chunks)),
            key=lambda index: (-float(scores[query_index, index]), str(chunks[index].chunk_id)),
        )[:limit]
        hits = tuple(
            RetrievedChunk(
                chunk_id=chunks[index].chunk_id,
                document_name=chunk_document_names[index],
                rank=rank,
                score=float(scores[query_index, index]),
                page_numbers=tuple(sorted({bbox.page_number for bbox in chunks[index].bboxes})),
            )
            for rank, index in enumerate(ordered, start=1)
        )
        results.append(
            QueryRetrieval(
                benchmark_query_id=query_id,
                document_name=query_document_names[query_index],
                hits=hits,
            )
        )
    return tuple(results)

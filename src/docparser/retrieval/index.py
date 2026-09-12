"""A local, reusable exact dense index with immutable embedding inputs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from docparser.ir.base import StrictIRModel
from docparser.ir.chunks import Chunk
from docparser.ir.models import DocumentIR
from docparser.retrieval.chunking import FIXED_CHUNKER_VERSION, FixedChunkConfig, fixed_token_chunks
from docparser.retrieval.context import (
    ContextBuilder,
    ContextConfig,
    EvidenceContext,
    EvidenceSource,
    SourceSpan,
    prepare_sources,
)
from docparser.retrieval.dense import (
    EmbeddingRuntime,
    QueryRetrieval,
    _numpy,
    exact_cosine_retrieval,
)


class IndexedChunk(StrictIRModel):
    chunk: Chunk
    source_spans: tuple[SourceSpan, ...]


class IndexManifest(StrictIRModel):
    version: Literal["fixed-evidence-index@1.0.0", "fixed-evidence-index@1.1.0"] = (
        "fixed-evidence-index@1.1.0"
    )
    chunker_version: str = FIXED_CHUNKER_VERSION
    chunk_config: FixedChunkConfig
    model_id: str
    model_digest: str
    tokenizer_id: str
    documents: tuple[tuple[str, str, str], ...]
    chunk_count: int
    vector_dimensions: int
    file_digests: dict[str, str]
    warnings: tuple[str, ...]
    document_warnings: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    table_alignment_counts: dict[str, int] = Field(default_factory=dict)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return f"sha256:{digest.hexdigest()}"


@dataclass(slots=True)
class DenseEvidenceIndex:
    manifest: IndexManifest
    entries: tuple[IndexedChunk, ...]
    sources: dict[str, EvidenceSource]
    vectors: Any

    def session(self, runtime: EmbeddingRuntime) -> QASearchSession:
        if (
            runtime.model_id != self.manifest.model_id
            or str(runtime.model_digest) != self.manifest.model_digest
            or runtime.tokenizer.tokenizer_id != self.manifest.tokenizer_id
        ):
            raise ValueError("index embedding model/tokenizer differs; rebuild the index")
        return QASearchSession(self, runtime)


class QASearchSession:
    """One corpus/model/context runtime for many questions; only queries are embedded."""

    def __init__(self, index: DenseEvidenceIndex, runtime: EmbeddingRuntime) -> None:
        self.index = index
        self.runtime = runtime
        self.builder = ContextBuilder(index.sources, runtime.tokenizer)
        self.spans = {str(entry.chunk.chunk_id): entry.source_spans for entry in index.entries}
        self.chunks = tuple(entry.chunk for entry in index.entries)
        self.names = tuple(
            index.sources[entry.source_spans[0].source_id].document_name for entry in index.entries
        )
        self.document_ids = frozenset(document[0] for document in index.manifest.documents)
        document_rows: dict[str, list[int]] = {identifier: [] for identifier in self.document_ids}
        for row, chunk in enumerate(self.chunks):
            document_rows[str(chunk.document_id)].append(row)
        self._document_rows = {key: tuple(rows) for key, rows in document_rows.items()}

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 5,
        document_ids: tuple[str, ...] = (),
        query_id: str | None = None,
    ) -> QueryRetrieval:
        if not question.strip():
            raise ValueError("question must contain text")
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        scope = tuple(sorted(set(document_ids)))
        if set(scope) - self.document_ids:
            raise ValueError("requested document IDs are not present in this index")
        if query_id is None:
            identity = json.dumps([question, scope], ensure_ascii=False) if scope else question
            query_id = hashlib.sha256(identity.encode()).hexdigest()
        rows = (
            tuple(sorted(row for identifier in scope for row in self._document_rows[identifier]))
            if scope
            else ()
        )
        if scope and not rows:
            return QueryRetrieval(benchmark_query_id=query_id, document_name="corpus", hits=())
        return exact_cosine_retrieval(
            query_ids=(query_id,),
            query_document_names=("corpus",),
            query_vectors=self.runtime.embed((question,)),
            chunks=tuple(self.chunks[row] for row in rows) if scope else self.chunks,
            chunk_document_names=tuple(self.names[row] for row in rows) if scope else self.names,
            chunk_vectors=self.index.vectors[list(rows)] if scope else self.index.vectors,
            top_k=top_k,
        )[0]

    def context(
        self,
        retrieval: QueryRetrieval,
        config: ContextConfig | None = None,
        *,
        document_ids: tuple[str, ...] = (),
    ) -> EvidenceContext:
        manifest = self.index.manifest
        warnings = manifest.warnings
        if document_ids:
            warnings = tuple(
                warning
                for identifier in sorted(set(document_ids))
                for warning in manifest.document_warnings.get(identifier, ())
            )
            if manifest.warnings and not manifest.document_warnings:
                warnings = (
                    "LEGACY_INDEX: partial-structure warnings have no document attribution",
                )
        return self.builder.build(retrieval, self.spans, config, warnings=warnings)


def build_evidence_index(
    documents: tuple[DocumentIR, ...],
    runtime: EmbeddingRuntime,
    output: Path,
    config: FixedChunkConfig | None = None,
) -> DenseEvidenceIndex:
    if (output / "manifest.json").exists():
        raise FileExistsError("index already exists; build into a new directory")
    config = config or FixedChunkConfig()
    ordered = sorted(documents, key=lambda document: str(document.document_id))
    if len({document.document_id for document in ordered}) != len(ordered):
        raise ValueError("index requires one revision per document")
    entries: list[IndexedChunk] = []
    sources: dict[str, EvidenceSource] = {}
    warnings: list[str] = []
    document_warnings: dict[str, tuple[str, ...]] = {}
    for document in ordered:
        chunks = tuple(
            chunk
            for chunk in fixed_token_chunks(document, runtime.tokenizer, config)
            if chunk.embedding_eligible
        )
        document_sources, spans = prepare_sources(document, runtime.tokenizer, chunks)
        sources.update(document_sources)
        entries.extend(
            IndexedChunk(chunk=chunk, source_spans=spans[str(chunk.chunk_id)]) for chunk in chunks
        )
        recovery = document.extensions.get("org.docparser.recovery")
        if isinstance(recovery, dict):
            warning = (
                f"{document.source.original_filename_safe}: PARTIAL_STRUCTURE; "
                f"missing_pages={recovery.get('missing_pages', [])}; "
                f"degraded_blocks={recovery.get('degraded_blocks', 0)}"
            )
            warnings.append(warning)
            document_warnings[str(document.document_id)] = (warning,)
    if not entries:
        raise ValueError("no renderable retrieval evidence; index was not created")
    vectors = runtime.embed(tuple(entry.chunk.text for entry in entries))
    np = _numpy()
    if vectors.ndim != 2 or vectors.shape[0] != len(entries):
        raise ValueError("embedding matrix does not match corpus")
    if not np.isfinite(vectors).all() or not np.allclose(
        np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5, atol=1e-6
    ):
        raise ValueError("index requires finite, normalized embeddings")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(entry.model_dump_json() + "\n")
    with (output / "sources.jsonl").open("w", encoding="utf-8") as handle:
        for source in sorted(sources.values(), key=lambda item: item.source_id):
            handle.write(source.model_dump_json() + "\n")
    np.save(output / "vectors.npy", vectors, allow_pickle=False)
    manifest = IndexManifest(
        chunk_config=config,
        model_id=runtime.model_id,
        model_digest=str(runtime.model_digest),
        tokenizer_id=runtime.tokenizer.tokenizer_id,
        documents=tuple(
            (str(doc.document_id), str(doc.revision_id), str(doc.source.sha256)) for doc in ordered
        ),
        chunk_count=len(entries),
        vector_dimensions=int(vectors.shape[1]),
        file_digests={
            name: _file_digest(output / name)
            for name in ("chunks.jsonl", "sources.jsonl", "vectors.npy")
        },
        warnings=tuple(warnings),
        document_warnings=document_warnings,
        table_alignment_counts=dict(
            Counter(
                source.table_map.alignment
                for source in sources.values()
                if source.table_map is not None
            )
        ),
    )
    # A directory becomes a loadable index only after all corpus files have been written.
    (output / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return DenseEvidenceIndex(manifest, tuple(entries), sources, vectors)


def load_evidence_index(path: Path) -> DenseEvidenceIndex:
    manifest = IndexManifest.model_validate_json((path / "manifest.json").read_bytes())
    for name in ("chunks.jsonl", "sources.jsonl", "vectors.npy"):
        if _file_digest(path / name) != manifest.file_digests[name]:
            raise ValueError(f"index file changed or incomplete: {name}")
    with (path / "chunks.jsonl").open(encoding="utf-8") as handle:
        entries = tuple(IndexedChunk.model_validate_json(line) for line in handle if line.strip())
    with (path / "sources.jsonl").open(encoding="utf-8") as handle:
        sources = {
            source.source_id: source
            for line in handle
            if line.strip()
            for source in (EvidenceSource.model_validate_json(line),)
        }
    vectors = _numpy().load(path / "vectors.npy", mmap_mode="r", allow_pickle=False)
    if len(entries) != manifest.chunk_count or vectors.shape != (
        manifest.chunk_count,
        manifest.vector_dimensions,
    ):
        raise ValueError("index cardinality differs from manifest")
    return DenseEvidenceIndex(manifest, entries, sources, vectors)

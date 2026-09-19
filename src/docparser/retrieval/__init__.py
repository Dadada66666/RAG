"""Minimal exact-dense retrieval experiment components."""

from docparser.retrieval.chunking import (
    FIXED_CHUNKER_VERSION,
    STRUCTURE_CHUNKER_VERSION,
    ChunkingError,
    FixedChunkConfig,
    RetrievalEvidenceView,
    StructureChunkConfig,
    Tokenizer,
    covered_source_block_ids,
    fixed_token_chunks,
    retrieval_evidence_view,
    structure_aware_chunks,
)
from docparser.retrieval.dense import (
    BgeM3Runtime,
    EmbeddingRuntime,
    QueryRetrieval,
    RetrievalRuntimeError,
    RetrievedChunk,
    exact_cosine_retrieval,
)
from docparser.retrieval.rerank import (
    BgeRerankerV2M3Runtime,
    RerankerRuntime,
    rerank_retrieval,
)

__all__ = [
    "ChunkingError",
    "BgeM3Runtime",
    "BgeRerankerV2M3Runtime",
    "EmbeddingRuntime",
    "FIXED_CHUNKER_VERSION",
    "FixedChunkConfig",
    "RetrievalEvidenceView",
    "STRUCTURE_CHUNKER_VERSION",
    "StructureChunkConfig",
    "Tokenizer",
    "covered_source_block_ids",
    "QueryRetrieval",
    "RetrievalRuntimeError",
    "RetrievedChunk",
    "RerankerRuntime",
    "exact_cosine_retrieval",
    "fixed_token_chunks",
    "retrieval_evidence_view",
    "rerank_retrieval",
    "structure_aware_chunks",
]

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

__all__ = [
    "ChunkingError",
    "BgeM3Runtime",
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
    "exact_cosine_retrieval",
    "fixed_token_chunks",
    "retrieval_evidence_view",
    "structure_aware_chunks",
]

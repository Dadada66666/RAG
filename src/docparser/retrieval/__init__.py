"""Minimal exact-dense retrieval experiment components."""

from docparser.retrieval.chunking import (
    FIXED_CHUNKER_VERSION,
    STRUCTURE_CHUNKER_VERSION,
    ChunkingError,
    FixedChunkConfig,
    StructureChunkConfig,
    Tokenizer,
    fixed_token_chunks,
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
    "STRUCTURE_CHUNKER_VERSION",
    "StructureChunkConfig",
    "Tokenizer",
    "QueryRetrieval",
    "RetrievalRuntimeError",
    "RetrievedChunk",
    "exact_cosine_retrieval",
    "fixed_token_chunks",
    "structure_aware_chunks",
]

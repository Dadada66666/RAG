from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from tests.retrieval_factory import CharacterTokenizer, make_retrieval_document, write_model_stub

from docparser.retrieval import (
    BgeM3Runtime,
    FixedChunkConfig,
    RetrievalRuntimeError,
    exact_cosine_retrieval,
    fixed_token_chunks,
)


class _FakeTokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        return [ord(character) for character in text]

    def decode(
        self,
        values: list[int],
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> str:
        assert skip_special_tokens is True
        assert clean_up_tokenization_spaces is False
        return "".join(chr(value) for value in values)


class _FakeSentenceTransformer:
    init_arguments: tuple[str, str, bool] | None = None

    def __init__(self, path: str, *, device: str, local_files_only: bool) -> None:
        type(self).init_arguments = (path, device, local_files_only)
        self.tokenizer = _FakeTokenizer()

    def encode(self, texts: list[str], **kwargs: object) -> Any:
        assert kwargs == {
            "batch_size": 2,
            "convert_to_numpy": True,
            "normalize_embeddings": False,
            "show_progress_bar": False,
        }
        return np.asarray([[3.0, 4.0] for _ in texts], dtype=np.float64)


def test_bge_m3_is_local_only_lazy_and_returns_normalized_float32(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    root = Path(str(tmp_path)) / "bge-m3"
    write_model_stub(root)
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    runtime = BgeM3Runtime(root, device="cpu", batch_size=2)
    assert _FakeSentenceTransformer.init_arguments is None
    vectors = runtime.embed(("a", "b"))

    assert _FakeSentenceTransformer.init_arguments == (str(root), "cpu", True)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), np.ones(2))
    assert runtime.tokenizer.decode(runtime.tokenizer.encode("abc")) == "abc"
    assert str(runtime.model_digest).startswith("sha256:")


def test_bge_m3_requires_an_existing_local_model_directory(tmp_path: object) -> None:
    from pathlib import Path

    with pytest.raises(RetrievalRuntimeError, match="local BGE-M3 model directory not found"):
        BgeM3Runtime(Path(str(tmp_path)) / "missing")


def test_bge_m3_optional_dependency_is_loaded_only_for_real_embedding(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    root = Path(str(tmp_path)) / "bge-m3"
    write_model_stub(root)
    runtime = BgeM3Runtime(root)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(RetrievalRuntimeError, match="sentence-transformers is required"):
        runtime.embed(("query",))


def test_exact_cosine_retrieval_uses_stable_chunk_id_ties_and_top_k() -> None:
    chunks = fixed_token_chunks(
        make_retrieval_document(),
        CharacterTokenizer(),
        FixedChunkConfig(target_tokens=24, overlap_tokens=0),
    )[:3]
    vectors = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

    result = exact_cosine_retrieval(
        query_ids=("query-1",),
        query_document_names=("academic/test",),
        query_vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
        chunks=chunks,
        chunk_document_names=("academic/test",) * 3,
        chunk_vectors=vectors,
        top_k=2,
    )

    tied_ids = sorted((chunks[0].chunk_id, chunks[1].chunk_id))
    assert [hit.chunk_id for hit in result[0].hits] == tied_ids
    assert [hit.rank for hit in result[0].hits] == [1, 2]

    with pytest.raises(ValueError, match="requires L2-normalized vectors"):
        exact_cosine_retrieval(
            query_ids=("query-1",),
            query_document_names=("academic/test",),
            query_vectors=np.asarray([[2.0, 0.0]], dtype=np.float32),
            chunks=chunks,
            chunk_document_names=("academic/test",) * 3,
            chunk_vectors=vectors,
            top_k=2,
        )

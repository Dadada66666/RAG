from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from tests.retrieval_factory import (
    FakeEmbeddingRuntime,
    make_retrieval_document,
    write_model_stub,
)

from docparser.retrieval.chunking import FixedChunkConfig
from docparser.retrieval.index import DenseEvidenceIndex, QASearchSession, build_evidence_index
from docparser.retrieval.rerank import BgeRerankerV2M3Runtime, rerank_retrieval


class FakeReranker:
    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self.scores = scores or {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        values = tuple(passages)
        self.calls.append((query, values))
        return tuple(self.scores.get(passage, 0.0) for passage in values)


class FakeCrossEncoder:
    init_arguments: tuple[str, str, bool] | None = None
    instances = 0

    def __init__(self, path: str, *, device: str, local_files_only: bool) -> None:
        type(self).init_arguments = (path, device, local_files_only)
        type(self).instances += 1

    def predict(self, pairs: list[tuple[str, str]], **kwargs: object) -> Any:
        assert kwargs == {
            "batch_size": 2,
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }
        return np.asarray([len(query) + len(passage) for query, passage in pairs])


def indexed(tmp_path: Path) -> tuple[DenseEvidenceIndex, FakeEmbeddingRuntime, QASearchSession]:
    runtime = FakeEmbeddingRuntime()
    index = build_evidence_index(
        (make_retrieval_document(),),
        runtime,
        tmp_path / "index",
        FixedChunkConfig(target_tokens=32, overlap_tokens=0),
    )
    assert len(index.entries) >= 6
    return index, runtime, index.session(runtime)


def chunk_texts(index: DenseEvidenceIndex) -> dict[str, str]:
    return {str(entry.chunk.chunk_id): entry.chunk.text for entry in index.entries}


def test_dense_retrieval_is_unchanged_without_reranker(tmp_path: Path) -> None:
    index, runtime, session = indexed(tmp_path)
    expected = session.retrieve("Revenue?", top_k=5)
    actual = index.session(runtime).retrieve(
        "Revenue?", top_k=5, reranker_candidate_k=20
    )
    assert actual == expected
    assert all(
        hit.dense_rank is None and hit.dense_score is None and hit.reranker_score is None
        for hit in actual.hits
    )


def test_reranker_promotes_dense_rank_six_and_preserves_provenance(tmp_path: Path) -> None:
    index, runtime, dense_session = indexed(tmp_path)
    dense = dense_session.retrieve("Revenue?", top_k=6)
    texts = chunk_texts(index)
    promoted = dense.hits[5]
    reranker = FakeReranker({texts[str(promoted.chunk_id)]: 10.0})
    result = index.session(runtime, reranker).retrieve(
        "Revenue?", top_k=5, reranker_candidate_k=6
    )

    assert [hit.chunk_id for hit in result.hits] == [
        promoted.chunk_id,
        *(hit.chunk_id for hit in dense.hits[:4]),
    ]
    assert len(result.hits) == 5
    first = result.hits[0]
    assert first.rank == 1 and first.score == 10.0
    assert first.dense_rank == 6 and first.dense_score == promoted.score
    assert first.reranker_score == 10.0
    assert first.document_name == promoted.document_name
    assert first.page_numbers == promoted.page_numbers
    assert reranker.calls == (
        []
        if not dense.hits
        else [
            (
                "Revenue?",
                tuple(texts[str(hit.chunk_id)] for hit in dense.hits),
            )
        ]
    )


def test_reranker_cannot_see_outside_dense_candidate_pool(tmp_path: Path) -> None:
    index, runtime, dense_session = indexed(tmp_path)
    dense = dense_session.retrieve("Revenue?", top_k=6)
    texts = chunk_texts(index)
    excluded = dense.hits[5]
    reranker = FakeReranker({texts[str(excluded.chunk_id)]: 100.0})
    result = index.session(runtime, reranker).retrieve(
        "Revenue?", top_k=5, reranker_candidate_k=5
    )

    assert excluded.chunk_id not in {hit.chunk_id for hit in result.hits}
    assert texts[str(excluded.chunk_id)] not in reranker.calls[0][1]
    assert [hit.chunk_id for hit in result.hits] == [hit.chunk_id for hit in dense.hits[:5]]


def test_reranker_ties_use_dense_rank_then_chunk_id(tmp_path: Path) -> None:
    index, _, session = indexed(tmp_path)
    candidates = session.retrieve("Revenue?", top_k=3)
    tied = candidates.model_copy(
        update={
            "hits": (
                candidates.hits[1].model_copy(update={"rank": 1}),
                candidates.hits[0].model_copy(update={"rank": 1}),
                candidates.hits[2],
            )
        }
    )
    result = rerank_retrieval(
        question="Revenue?",
        candidates=tied,
        chunk_text_by_id=chunk_texts(index),
        runtime=FakeReranker(),
        top_k=3,
    )
    expected_tied_ids = sorted((tied.hits[0].chunk_id, tied.hits[1].chunk_id))
    assert [hit.chunk_id for hit in result.hits[:2]] == expected_tied_ids
    assert result.hits[2].chunk_id == candidates.hits[2].chunk_id
    assert [hit.rank for hit in result.hits] == [1, 2, 3]


def test_reranker_candidate_k_must_cover_final_top_k(tmp_path: Path) -> None:
    index, runtime, _ = indexed(tmp_path)
    with pytest.raises(ValueError, match="reranker_candidate_k must be >= top_k"):
        index.session(runtime, FakeReranker()).retrieve(
            "Revenue?", top_k=5, reranker_candidate_k=4
        )


def test_final_top_k_caps_a_larger_dense_candidate_pool(tmp_path: Path) -> None:
    index, runtime, _ = indexed(tmp_path)
    reranker = FakeReranker()
    result = index.session(runtime, reranker).retrieve(
        "Revenue?", top_k=5, reranker_candidate_k=20
    )
    assert len(result.hits) == 5
    assert len(reranker.calls) == 1
    assert len(reranker.calls[0][1]) == len(index.entries)


def test_bge_reranker_is_local_only_lazy_batched_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bge-reranker-v2-m3"
    write_model_stub(root)
    FakeCrossEncoder.init_arguments = None
    FakeCrossEncoder.instances = 0
    module = ModuleType("sentence_transformers")
    module.CrossEncoder = FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    runtime = BgeRerankerV2M3Runtime(root, device="cuda", batch_size=2)
    assert FakeCrossEncoder.init_arguments is None
    first = runtime.score("q", ("a", "bb"))
    second = runtime.score("q", ("ccc",))

    assert first == (2.0, 3.0) and second == (4.0,)
    assert FakeCrossEncoder.init_arguments == (str(root), "cuda", True)
    assert FakeCrossEncoder.instances == 1

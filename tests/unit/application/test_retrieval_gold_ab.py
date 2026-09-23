"""Offline evidence-level A/B over existing indexes and independently frozen gold."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from tests.ir_factory import TEST_NAMESPACE
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document

from docparser.application.retrieval_gold_ab import run_retrieval_gold_ab
from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import (
    RetrievalGoldManifest,
    RetrievalGoldQuery,
    load_retrieval_gold_manifest,
    retrieval_gold_index_identity,
)
from docparser.ir.ids import ChunkId, generate_uuid5_id
from docparser.retrieval.chunking import FixedChunkConfig, StructureChunkConfig
from docparser.retrieval.dense import QueryRetrieval
from docparser.retrieval.index import (
    DenseEvidenceIndex,
    build_evidence_index,
    load_evidence_index,
)


class _FakeReranker:
    model_id = "test/reranker"
    model_digest = "sha256:" + "1" * 64

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        self.calls.append((query, tuple(passages)))
        return tuple(float(len(passage)) for passage in passages)


def _query(identifier: str) -> RetrievalGroundTruth:
    return RetrievalGroundTruth(
        benchmark_query_id=identifier,
        source_dataset="synthetic",
        source_dataset_item_id=identifier,
        document_name="academic/synthetic-report",
        question=f"Revenue question {identifier}",
        answer="synthetic answer",
        evidence_type=RetrievalEvidenceType.TABLE,
        evidence_contexts=("synthetic evidence",),
        evidence_page_indices=(0,),
        document_type_or_domain="academic",
    )


def _write_gold(path: Path, index: DenseEvidenceIndex, query_ids: tuple[str, ...]) -> None:
    gold = RetrievalGoldManifest(
        index_identity=retrieval_gold_index_identity(index.manifest),
        queries=tuple(
            RetrievalGoldQuery(
                query_id=identifier,
                acceptable_chunk_ids=(index.entries[0].chunk.chunk_id,),
            )
            for identifier in query_ids
        ),
    )
    path.write_text(gold.model_dump_json(), encoding="utf-8")


def _inputs(tmp_path: Path) -> tuple[dict[str, Path], FakeEmbeddingRuntime, _FakeReranker]:
    document = make_retrieval_document()
    runtime = FakeEmbeddingRuntime()
    fixed_path = tmp_path / "fixed-index"
    structure_path = tmp_path / "structure-index"
    fixed = build_evidence_index(
        (document,),
        runtime,
        fixed_path,
        FixedChunkConfig(target_tokens=32, overlap_tokens=0),
    )
    structure = build_evidence_index(
        (document,),
        runtime,
        structure_path,
        StructureChunkConfig(target_tokens=64),
        chunking_policy="STRUCTURE",
    )
    query_ids = ("synthetic-one", "synthetic-two")
    queries_path = tmp_path / "queries.jsonl"
    queries_path.write_text(
        "".join(_query(identifier).model_dump_json() + "\n" for identifier in query_ids),
        encoding="utf-8",
    )
    fixed_gold = tmp_path / "fixed-gold.json"
    structure_gold = tmp_path / "structure-gold.json"
    _write_gold(fixed_gold, fixed, query_ids)
    _write_gold(structure_gold, structure, query_ids)
    document_map = tmp_path / "document-map.json"
    document_map.write_text(
        json.dumps({_query(query_ids[0]).document_name: str(document.document_id)}),
        encoding="utf-8",
    )
    runtime.calls.clear()
    return (
        {
            "fixed_index_path": fixed_path,
            "structure_index_path": structure_path,
            "queries_path": queries_path,
            "fixed_gold_path": fixed_gold,
            "structure_gold_path": structure_gold,
            "document_map_path": document_map,
            "output_dir": tmp_path / "results",
        },
        runtime,
        _FakeReranker(),
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_same_queries_use_one_embedding_call_and_write_evidence_context_artifacts(
    tmp_path: Path,
) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    outcome = run_retrieval_gold_ab(
        fixed_index_path=paths["fixed_index_path"],
        structure_index_path=paths["structure_index_path"],
        queries_path=paths["queries_path"],
        fixed_gold_path=paths["fixed_gold_path"],
        structure_gold_path=paths["structure_gold_path"],
        output_dir=paths["output_dir"],
        runtime=runtime,
        reranker=reranker,
    )

    assert outcome.query_count == 2
    assert runtime.calls == [("Revenue question synthetic-one", "Revenue question synthetic-two")]
    assert len(reranker.calls) == 4
    manifest = json.loads((paths["output_dir"] / "run.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == "corpus"
    assert manifest["candidate_k"] == 20 and manifest["top_k"] == 5
    assert manifest["context_config"]["max_tokens"] == 4096
    assert manifest["reranker_model_id"] == reranker.model_id
    assert manifest["fixed_index_identity"] != manifest["structure_index_identity"]
    comparison = json.loads((paths["output_dir"] / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["structure_minus_fixed"]["dense"]["evidence_recall_at_20"] == 0.0
    paired = _records(paths["output_dir"] / "paired.jsonl")
    assert [item["query_id"] for item in paired] == ["synthetic-one", "synthetic-two"]
    for item in paired:
        fixed_observation, structure_observation = item["fixed"], item["structure"]
        assert isinstance(fixed_observation, dict)
        assert isinstance(structure_observation, dict)
        assert fixed_observation["query_id"] == structure_observation["query_id"]
    for name in ("fixed", "structure"):
        directory = paths["output_dir"] / name
        assert len(_records(directory / "dense.jsonl")) == 2
        finals = [
            QueryRetrieval.model_validate_json(line)
            for line in (directory / "reranked.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(finals) == 2
        assert all(len(result.hits) <= 5 for result in finals)
        assert all(
            hit.dense_rank is not None and hit.dense_score is not None
            for result in finals
            for hit in result.hits
        )
        assert len(_records(directory / "context.jsonl")) == 2
        observations = _records(directory / "per-query.jsonl")
        assert len(observations) == 2
        assert all(
            item["evidence_sufficiency"] == "REQUIRES_INDEPENDENT_REVIEW"
            for item in observations
        )
        metrics = json.loads((directory / "dense.metrics.json").read_text(encoding="utf-8"))
        assert metrics["query_count"] == 2
        assert metrics["hit_count_at_20"] == 2
        assert metrics["evidence_recall_at_20"] == 1.0
        assert metrics["by_evidence_type"]["TABLE"]["query_count"] == 2
        assert metrics["by_evidence_type"]["TEXT"]["query_count"] == 0
        assert metrics["by_evidence_type"]["TEXT"]["evidence_recall_at_5"] is None


def test_frozen_gold_must_match_each_index_and_the_full_query_set(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    paths["structure_gold_path"].write_bytes(paths["fixed_gold_path"].read_bytes())
    with pytest.raises(ValueError, match="Structure gold index identity differs"):
        run_retrieval_gold_ab(
            fixed_index_path=paths["fixed_index_path"],
            structure_index_path=paths["structure_index_path"],
            queries_path=paths["queries_path"],
            fixed_gold_path=paths["fixed_gold_path"],
            structure_gold_path=paths["structure_gold_path"],
            output_dir=paths["output_dir"],
            runtime=runtime,
            reranker=reranker,
        )
    assert not paths["output_dir"].exists()
    assert not runtime.calls


def test_incomplete_gold_cannot_shrink_the_planned_query_denominator(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    gold_path = paths["structure_gold_path"]
    gold = RetrievalGoldManifest.model_validate_json(gold_path.read_bytes())
    gold_path.write_text(
        gold.model_copy(update={"queries": gold.queries[:1]}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="gold query IDs differ from the complete planned"):
        run_retrieval_gold_ab(
            fixed_index_path=paths["fixed_index_path"],
            structure_index_path=paths["structure_index_path"],
            queries_path=paths["queries_path"],
            fixed_gold_path=paths["fixed_gold_path"],
            structure_gold_path=paths["structure_gold_path"],
            output_dir=paths["output_dir"],
            runtime=runtime,
            reranker=reranker,
        )
    assert not paths["output_dir"].exists()
    assert not runtime.calls


def test_unapproved_chunk_id_fails_before_any_retrieval(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    gold = RetrievalGoldManifest.model_validate_json(paths["fixed_gold_path"].read_bytes())
    missing = generate_uuid5_id(ChunkId, TEST_NAMESPACE, "not-indexed")
    changed = gold.model_copy(
        update={
            "queries": (
                gold.queries[0].model_copy(update={"acceptable_chunk_ids": (missing,)}),
                gold.queries[1],
            )
        }
    )
    paths["fixed_gold_path"].write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="gold contains a chunk absent"):
        run_retrieval_gold_ab(
            fixed_index_path=paths["fixed_index_path"],
            structure_index_path=paths["structure_index_path"],
            queries_path=paths["queries_path"],
            fixed_gold_path=paths["fixed_gold_path"],
            structure_gold_path=paths["structure_gold_path"],
            output_dir=paths["output_dir"],
            runtime=runtime,
            reranker=reranker,
        )
    assert not runtime.calls


def test_gold_order_can_differ_from_planned_query_order(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    for name in ("fixed", "structure"):
        gold_path = paths[f"{name}_gold_path"]
        gold = RetrievalGoldManifest.model_validate_json(gold_path.read_bytes())
        index = load_evidence_index(paths[f"{name}_index_path"])
        later_chunk = index.entries[-1].chunk.chunk_id
        changed = gold.model_copy(
            update={
                "queries": (
                    gold.queries[1].model_copy(update={"acceptable_chunk_ids": (later_chunk,)}),
                    gold.queries[0],
                )
            }
        )
        gold_path.write_text(changed.model_dump_json(), encoding="utf-8")

    run_retrieval_gold_ab(
        fixed_index_path=paths["fixed_index_path"],
        structure_index_path=paths["structure_index_path"],
        queries_path=paths["queries_path"],
        fixed_gold_path=paths["fixed_gold_path"],
        structure_gold_path=paths["structure_gold_path"],
        output_dir=paths["output_dir"],
        runtime=runtime,
        reranker=reranker,
    )
    for name in ("fixed", "structure"):
        directory = paths["output_dir"] / name
        observations = _records(directory / "per-query.jsonl")
        assert [item["query_id"] for item in observations] == ["synthetic-one", "synthetic-two"]
        finals = [
            QueryRetrieval.model_validate_json(line)
            for line in (directory / "reranked.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        target = load_retrieval_gold_manifest(paths[f"{name}_gold_path"]).queries[0]
        matching = [
            hit.rank
            for hit in finals[1].hits
            if hit.chunk_id in target.acceptable_chunk_ids
        ]
        assert observations[1]["reranked_evidence_rank"] == (matching[0] if matching else None)


def test_document_scope_requires_explicit_name_to_id_map(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    inputs = dict(
        fixed_index_path=paths["fixed_index_path"],
        structure_index_path=paths["structure_index_path"],
        queries_path=paths["queries_path"],
        fixed_gold_path=paths["fixed_gold_path"],
        structure_gold_path=paths["structure_gold_path"],
        output_dir=paths["output_dir"],
        runtime=runtime,
        reranker=reranker,
    )
    with pytest.raises(ValueError, match="requires an explicit document map"):
        run_retrieval_gold_ab(**inputs, scope="document")  # type: ignore[arg-type]
    assert not runtime.calls

    outcome = run_retrieval_gold_ab(
        **inputs,  # type: ignore[arg-type]
        scope="document",
        document_map_path=paths["document_map_path"],
    )
    assert outcome.query_count == 2
    assert outcome.comparison["scope"] == "document"
    assert len(runtime.calls) == 1


def test_repeated_run_is_deterministic_and_does_not_overwrite_results(tmp_path: Path) -> None:
    paths, runtime, reranker = _inputs(tmp_path)
    inputs = dict(
        fixed_index_path=paths["fixed_index_path"],
        structure_index_path=paths["structure_index_path"],
        queries_path=paths["queries_path"],
        fixed_gold_path=paths["fixed_gold_path"],
        structure_gold_path=paths["structure_gold_path"],
        runtime=runtime,
        reranker=reranker,
    )
    first = paths["output_dir"]
    second = tmp_path / "repeat"
    run_retrieval_gold_ab(**inputs, output_dir=first)  # type: ignore[arg-type]
    run_retrieval_gold_ab(**inputs, output_dir=second)  # type: ignore[arg-type]
    for relative in (
        "run.json",
        "comparison.json",
        "paired.jsonl",
        "fixed/dense.jsonl",
        "fixed/reranked.jsonl",
        "fixed/context.jsonl",
        "structure/dense.jsonl",
        "structure/reranked.jsonl",
        "structure/context.jsonl",
    ):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
    with pytest.raises(FileExistsError, match="not empty"):
        run_retrieval_gold_ab(**inputs, output_dir=first)  # type: ignore[arg-type]

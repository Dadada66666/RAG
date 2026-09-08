from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from tests.retrieval_factory import (
    FakeEmbeddingRuntime,
    make_paddle_like_unresolved_document,
    make_retrieval_document,
)

from docparser.application.retrieval_ab import run_retrieval_ab
from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import RetrievalSlice
from docparser.ir.chunks import Chunk
from docparser.ir.serialization import dump_canonical_json
from docparser.retrieval import (
    FixedChunkConfig,
    StructureChunkConfig,
    fixed_token_chunks,
    structure_aware_chunks,
)


def _query(
    identifier: str, evidence: RetrievalEvidenceType, document_name: str
) -> RetrievalGroundTruth:
    return RetrievalGroundTruth(
        benchmark_query_id=identifier,
        source_dataset="synthetic",
        source_dataset_item_id=f"source-{identifier}",
        document_name=document_name,
        question=identifier,
        answer="answer",
        evidence_type=evidence,
        evidence_contexts=("context",),
        evidence_page_indices=(0,),
        document_type_or_domain="academic",
    )


def test_synthetic_end_to_end_ab_writes_reproducible_artifacts(tmp_path: Path) -> None:
    ir_root = tmp_path / "ir"
    document_dir = ir_root / "academic" / "test"
    document_dir.mkdir(parents=True)
    (document_dir / "document.ir.json").write_bytes(
        dump_canonical_json(make_retrieval_document())
    )
    queries = (
        _query("revenue", RetrievalEvidenceType.TEXT, "academic/test"),
        _query("table", RetrievalEvidenceType.TABLE, "academic/test"),
        _query("risk", RetrievalEvidenceType.READING_ORDER, "academic/test"),
        _query("missing", RetrievalEvidenceType.TEXT, "law/missing"),
    )
    query_path = tmp_path / "queries.jsonl"
    query_path.write_text(
        "\n".join(
            json.dumps(query.model_dump(mode="json"), sort_keys=True) for query in queries
        )
        + "\n",
        encoding="utf-8",
    )
    runtime = FakeEmbeddingRuntime()
    output = tmp_path / "run"

    outcome = run_retrieval_ab(
        ir_root=ir_root,
        queries_path=query_path,
        output_dir=output,
        model_path=None,
        fixed_config=FixedChunkConfig(target_tokens=64, overlap_tokens=8),
        structure_config=StructureChunkConfig(target_tokens=80, hard_max_tokens=200),
        top_k=10,
        source_commit="synthetic-commit",
        embedding_runtime=runtime,
    )

    assert [query.benchmark_query_id for query in outcome.eligible_queries] == [
        "revenue",
        "table",
        "risk",
    ]
    assert outcome.excluded_query_ids == ("missing",)
    assert len(runtime.calls) == 3
    assert runtime.calls[0] == ("revenue", "table", "risk")
    assert outcome.fixed_metrics.metrics_by_slice[RetrievalSlice.ALL].query_count == 3
    assert outcome.structure_metrics.metrics_by_slice[RetrievalSlice.ALL].query_count == 3
    expected = {
        "manifest.json",
        "queries.jsonl",
        "fixed/chunks.jsonl",
        "fixed/embeddings.npy",
        "fixed/retrieval.jsonl",
        "fixed/metrics.json",
        "structure/chunks.jsonl",
        "structure/embeddings.npy",
        "structure/retrieval.jsonl",
        "structure/metrics.json",
        "comparison.json",
        "report.md",
    }
    assert {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    } == expected
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["eligible_query_ids"] == ["revenue", "table", "risk"]
    assert manifest["excluded_query_ids"] == ["missing"]
    assert manifest["embedding_dtype"] == "float32"
    assert manifest["fixed_embedding_chunk_count"] > 0
    assert manifest["structure_embedding_chunk_count"] > 0
    assert manifest["structure_average_tokens_per_chunk"] > 0
    assert manifest["structure_median_tokens_per_chunk"] > 0
    assert manifest["structure_table_chunk_count"] > 0
    assert manifest["structure_normal_child_count"] > 0
    assert manifest["expected_retrieval_source_block_count"] > 0
    assert manifest["unresolved_retrieval_block_count"] == 1
    assert manifest["fixed_table_source_block_coverage_count"] == 1
    assert manifest["structure_table_source_block_coverage_count"] == 1
    assert manifest["experiment_version"] == "rag-retrieval-ab@1.1.0"
    assert manifest["fixed_chunker_version"] == "ir-fixed-token@1.1.0"
    assert manifest["structure_chunker_version"] == "ir-structure-aware@2.1.0"
    assert np.load(output / "fixed" / "embeddings.npy", allow_pickle=False).dtype == np.float32
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "PageHitRate@1" in report
    assert "Structure average tokens/chunk" in report
    assert "TableSourceExposure@1" in report

    repeated = run_retrieval_ab(
        ir_root=ir_root,
        queries_path=query_path,
        output_dir=tmp_path / "repeated-run",
        model_path=None,
        fixed_config=FixedChunkConfig(target_tokens=64, overlap_tokens=8),
        structure_config=StructureChunkConfig(target_tokens=80, hard_max_tokens=200),
        top_k=10,
        source_commit="synthetic-commit",
        embedding_runtime=FakeEmbeddingRuntime(),
    )
    assert [chunk.chunk_id for chunk in repeated.fixed_chunks] == [
        chunk.chunk_id for chunk in outcome.fixed_chunks
    ]
    assert [chunk.chunk_id for chunk in repeated.structure_chunks] == [
        chunk.chunk_id for chunk in outcome.structure_chunks
    ]
    assert repeated.manifest == outcome.manifest


def _write_paddle_like_case(tmp_path: Path) -> tuple[Path, Path]:
    ir_root = tmp_path / "ir"
    document_dir = ir_root / "academic" / "paddle-like"
    document_dir.mkdir(parents=True)
    (document_dir / "document.ir.json").write_bytes(
        dump_canonical_json(make_paddle_like_unresolved_document())
    )
    query_path = tmp_path / "queries.jsonl"
    query = _query("table", RetrievalEvidenceType.TABLE, "academic/paddle-like")
    query_path.write_text(
        json.dumps(query.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ir_root, query_path


def test_realistic_paddle_like_run_exposes_all_feature_activation(tmp_path: Path) -> None:
    ir_root, query_path = _write_paddle_like_case(tmp_path)
    outcome = run_retrieval_ab(
        ir_root=ir_root,
        queries_path=query_path,
        output_dir=tmp_path / "run",
        model_path=None,
        fixed_config=FixedChunkConfig(target_tokens=64, overlap_tokens=8),
        structure_config=StructureChunkConfig(target_tokens=100, hard_max_tokens=220),
        embedding_runtime=FakeEmbeddingRuntime(),
    )

    assert outcome.manifest["canonical_table_block_count"] == 2
    assert outcome.manifest["table_block_unresolved_count"] == 2
    assert outcome.manifest["fixed_table_source_block_coverage_count"] == 2
    assert outcome.manifest["structure_table_source_block_coverage_count"] == 2
    table_chunk_count = outcome.manifest["structure_table_chunk_count"]
    header_aware_count = outcome.manifest["structure_header_aware_table_chunk_count"]
    compact_count = outcome.manifest["structure_compact_table_chunk_count"]
    assert isinstance(table_chunk_count, int)
    assert isinstance(header_aware_count, int)
    assert isinstance(compact_count, int)
    assert table_chunk_count > 0
    assert outcome.manifest["structure_bound_caption_table_count"] == 1
    assert outcome.manifest["structure_unbound_caption_table_count"] == 1
    assert header_aware_count > 0
    assert compact_count > 0


@pytest.mark.parametrize("system", ["fixed", "structure"])
def test_runner_fails_when_either_representation_drops_expected_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str
) -> None:
    ir_root, query_path = _write_paddle_like_case(tmp_path)
    original = cast(
        Callable[..., tuple[Chunk, ...]],
        fixed_token_chunks if system == "fixed" else structure_aware_chunks,
    )

    def drop_unresolved(*args: Any, **kwargs: Any) -> tuple[Chunk, ...]:
        chunks = original(*args, **kwargs)
        return tuple(
            chunk
            for chunk in chunks
            if chunk.metadata.get("reading_order_policy") != "ISOLATED_UNRESOLVED"
        )

    monkeypatch.setattr(
        "docparser.application.retrieval_ab."
        + ("fixed_token_chunks" if system == "fixed" else "structure_aware_chunks"),
        drop_unresolved,
    )
    with pytest.raises(ValueError, match=f"missing_in_{system}"):
        run_retrieval_ab(
            ir_root=ir_root,
            queries_path=query_path,
            output_dir=tmp_path / "run",
            model_path=None,
            fixed_config=FixedChunkConfig(target_tokens=64, overlap_tokens=8),
            structure_config=StructureChunkConfig(target_tokens=100, hard_max_tokens=220),
            embedding_runtime=FakeEmbeddingRuntime(),
        )


def test_runner_fails_when_linked_tables_produce_no_structure_table_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir_root, query_path = _write_paddle_like_case(tmp_path)
    original = structure_aware_chunks

    def erase_table_activation(*args: Any, **kwargs: Any) -> tuple[Chunk, ...]:
        return tuple(
            chunk.model_copy(update={"chunk_type": "CHILD"})
            if chunk.chunk_type.value == "TABLE"
            else chunk
            for chunk in original(*args, **kwargs)
        )

    monkeypatch.setattr(
        "docparser.application.retrieval_ab.structure_aware_chunks",
        erase_table_activation,
    )
    with pytest.raises(ValueError, match="TABLE activation failed"):
        run_retrieval_ab(
            ir_root=ir_root,
            queries_path=query_path,
            output_dir=tmp_path / "run",
            model_path=None,
            fixed_config=FixedChunkConfig(target_tokens=64, overlap_tokens=8),
            structure_config=StructureChunkConfig(target_tokens=100, hard_max_tokens=220),
            embedding_runtime=FakeEmbeddingRuntime(),
        )

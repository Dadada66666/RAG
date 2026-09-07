from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document

from docparser.application.retrieval_ab import run_retrieval_ab
from docparser.evaluation.ohr import RetrievalEvidenceType, RetrievalGroundTruth
from docparser.evaluation.retrieval import RetrievalSlice
from docparser.ir.serialization import dump_canonical_json
from docparser.retrieval import FixedChunkConfig, StructureChunkConfig


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
    assert np.load(output / "fixed" / "embeddings.npy", allow_pickle=False).dtype == np.float32
    assert "PageHitRate@1" in (output / "report.md").read_text(encoding="utf-8")

"""Development parsing evaluation API."""

from docparser.evaluation.benchmark import (
    load_manifest,
    run_parsing_benchmark,
    summarize_cases,
    write_benchmark_report,
)
from docparser.evaluation.metrics import (
    compute_normalized_edit_similarity,
    match_truth_blocks,
    normalized_edit_similarity,
    score_numeric_predictions,
    score_outcome,
    score_table_predictions,
)
from docparser.evaluation.ohr import (
    OHRSelectionConfig,
    PreparedOHRSubset,
    prepare_ohr_rag_core,
    write_ohr_subset,
)
from docparser.evaluation.retrieval import (
    EvidenceRankResult,
    EvidenceRankStatus,
    EvidenceRetrievalMetrics,
    EvidenceRetrievalReport,
    PageRetrievalMetrics,
    PageRetrievalReport,
    RetrievalGoldIndexIdentity,
    RetrievalGoldManifest,
    RetrievalGoldQuery,
    RetrievalSlice,
    evaluate_page_retrieval,
    evaluate_retrieval_gold,
    load_retrieval_gold_manifest,
    retrieval_gold_index_identity,
)

__all__ = [
    "compute_normalized_edit_similarity",
    "EvidenceRankResult",
    "EvidenceRankStatus",
    "EvidenceRetrievalMetrics",
    "EvidenceRetrievalReport",
    "evaluate_retrieval_gold",
    "load_manifest",
    "load_retrieval_gold_manifest",
    "match_truth_blocks",
    "normalized_edit_similarity",
    "OHRSelectionConfig",
    "PageRetrievalMetrics",
    "PageRetrievalReport",
    "PreparedOHRSubset",
    "prepare_ohr_rag_core",
    "run_parsing_benchmark",
    "RetrievalGoldIndexIdentity",
    "RetrievalGoldManifest",
    "RetrievalGoldQuery",
    "RetrievalSlice",
    "retrieval_gold_index_identity",
    "score_numeric_predictions",
    "score_outcome",
    "score_table_predictions",
    "summarize_cases",
    "write_benchmark_report",
    "write_ohr_subset",
    "evaluate_page_retrieval",
]

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

__all__ = [
    "compute_normalized_edit_similarity",
    "load_manifest",
    "match_truth_blocks",
    "normalized_edit_similarity",
    "OHRSelectionConfig",
    "PreparedOHRSubset",
    "prepare_ohr_rag_core",
    "run_parsing_benchmark",
    "score_numeric_predictions",
    "score_outcome",
    "score_table_predictions",
    "summarize_cases",
    "write_benchmark_report",
    "write_ohr_subset",
]

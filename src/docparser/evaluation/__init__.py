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

__all__ = [
    "compute_normalized_edit_similarity",
    "load_manifest",
    "match_truth_blocks",
    "normalized_edit_similarity",
    "run_parsing_benchmark",
    "score_numeric_predictions",
    "score_outcome",
    "score_table_predictions",
    "summarize_cases",
    "write_benchmark_report",
]

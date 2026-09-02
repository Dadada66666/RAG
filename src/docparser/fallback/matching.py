"""Deterministic one-target table candidate matching for fallback MVP."""

from __future__ import annotations

from dataclasses import dataclass

from docparser.ir.geometry import BBox
from docparser.ir.tables import Table
from docparser.preflight import extract_numeric_tokens


@dataclass(frozen=True, slots=True)
class TableMatch:
    candidate: Table | None
    status: str
    best_score: float
    runner_up_score: float | None


def _iou(left: BBox, right: BBox) -> float:
    x0 = max(left.x0, right.x0)
    y0 = max(left.y0, right.y0)
    x1 = min(left.x1, right.x1)
    y1 = min(left.y1, right.y1)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if intersection == 0.0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _score(baseline: Table, candidate: Table) -> float:
    baseline_bbox = baseline.segments[0].bbox
    candidate_bbox = candidate.segments[0].bbox
    geometry = _iou(baseline_bbox, candidate_bbox)
    dimension_delta = abs(baseline.logical_row_count - candidate.logical_row_count) + abs(
        baseline.logical_column_count - candidate.logical_column_count
    )
    dimensions = 1.0 / (1.0 + dimension_delta)
    baseline_text = {cell.text.strip().casefold() for cell in baseline.cells if cell.text.strip()}
    candidate_text = {cell.text.strip().casefold() for cell in candidate.cells if cell.text.strip()}
    text = _jaccard(baseline_text, candidate_text)
    baseline_numbers = {
        token.normalized for cell in baseline.cells for token in extract_numeric_tokens(cell.text)
    }
    candidate_numbers = {
        token.normalized for cell in candidate.cells for token in extract_numeric_tokens(cell.text)
    }
    numerics = _jaccard(baseline_numbers, candidate_numbers)
    return round(0.45 * geometry + 0.25 * dimensions + 0.2 * text + 0.1 * numerics, 8)


def match_table_candidate(
    baseline: Table,
    candidates: tuple[Table, ...],
    *,
    minimum_score: float,
    winner_margin: float,
) -> TableMatch:
    """Return a unique compatible candidate or an explicit conflict/no-match result."""

    if not candidates:
        return TableMatch(None, "NO_CANDIDATE", 0.0, None)
    ranked = sorted(
        (
            (_score(baseline, candidate), str(candidate.table_id), candidate)
            for candidate in candidates
        ),
        key=lambda item: (-item[0], item[1]),
    )
    best_score, _, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else None
    if best_score < minimum_score:
        return TableMatch(None, "NO_CANDIDATE", best_score, runner_up)
    if runner_up is not None and best_score - runner_up < winner_margin:
        return TableMatch(None, "CONFLICT", best_score, runner_up)
    return TableMatch(best, "MATCHED", best_score, runner_up)

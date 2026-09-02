"""Deterministic project metrics with explicit identity, status, and denominators."""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from docparser.application.parsing import ParseOutcome
from docparser.evaluation.models import (
    EvaluationDenominators,
    MetricStatus,
    MetricValues,
    PageAnnotation,
    TableCellTruth,
    TableTruth,
)
from docparser.ir.enums import BlockType, ReadingOrderStatus
from docparser.ir.geometry import BBox
from docparser.ir.models import Block, DocumentIR
from docparser.ir.tables import Table, TableCell
from docparser.preflight import extract_numeric_tokens

MAX_EDIT_DISTANCE_CELLS = 4_000_000
TABLE_MATCH_MINIMUM = 0.45
PROJECT_METRIC_IMPLEMENTATION_VERSION = "project-parsing-metrics@2.1.0"
TEXT_ASSEMBLY_PROFILE = "canonical-reading-flow-with-logical-tables@1.0.0"
_TIE_EPSILON = 1e-12
_RETRIEVAL_BLOCK_TYPES = {
    BlockType.TITLE,
    BlockType.HEADING,
    BlockType.PARAGRAPH,
    BlockType.LIST,
    BlockType.LIST_ITEM,
    BlockType.TABLE,
    BlockType.FIGURE,
    BlockType.FIGURE_CAPTION,
    BlockType.EQUATION,
    BlockType.CODE,
    BlockType.QUOTE,
    BlockType.FOOTNOTE,
}


@dataclass(frozen=True, slots=True)
class TextMetricResult:
    status: MetricStatus
    similarity: float | None
    scored_characters: int
    incomplete_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TableScore:
    matches: dict[str, Table]
    detection_tp: int
    detection_fp: int
    detection_fn: int
    logical_rows_correct: int
    logical_rows_expected: int
    logical_columns_correct: int
    logical_columns_expected: int
    cells_text_correct: int
    cells_expected: int
    unexpected_cells: int
    rowspans_correct: int
    colspans_correct: int
    occupied_grids_valid: int
    occupied_grids_expected: int
    segments_covered: int
    segments_expected: int
    continuations_correct: int
    continuations_expected: int


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def compute_normalized_edit_similarity(
    expected: str,
    actual: str,
    *,
    max_matrix_cells: int = MAX_EDIT_DISTANCE_CELLS,
) -> TextMetricResult:
    """Return exact edit similarity or an explicit incomplete metric."""

    if max_matrix_cells <= 0:
        raise ValueError("max_matrix_cells must be positive")
    left = _normalized_text(expected)
    right = _normalized_text(actual)
    required_cells = (len(left) + 1) * (len(right) + 1)
    if required_cells > max_matrix_cells:
        return TextMetricResult(
            status=MetricStatus.INCOMPLETE,
            similarity=None,
            scored_characters=0,
            incomplete_reason=(
                f"EDIT_DISTANCE_BUDGET_EXCEEDED:{required_cells}>{max_matrix_cells}"
            ),
        )
    denominator = max(len(left), len(right), 1)
    return TextMetricResult(
        status=MetricStatus.COMPLETE,
        similarity=max(0.0, 1.0 - _edit_distance(left, right) / denominator),
        scored_characters=len(left) + len(right),
    )


def normalized_edit_similarity(expected: str, actual: str) -> float:
    """Compatibility helper for bounded complete inputs."""

    result = compute_normalized_edit_similarity(expected, actual)
    if result.status is not MetricStatus.COMPLETE or result.similarity is None:
        raise ValueError(result.incomplete_reason or "text metric incomplete")
    return result.similarity


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return 0.0 if precision + recall == 0.0 else 2 * precision * recall / (precision + recall)


def pairwise_order_accuracy(order: Sequence[str], pairs: Sequence[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    positions = {value: index for index, value in enumerate(order)}
    correct = sum(
        before in positions and after in positions and positions[before] < positions[after]
        for before, after in pairs
    )
    return correct / len(pairs)


def _bbox_iou(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    intersection = width * height
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union > 0.0 else 0.0


def _blocks_for_page(document: DocumentIR, page_number: int) -> tuple[Block, ...]:
    return document.pages[page_number - 1].blocks


def _block_compatibility(
    truth_type: BlockType,
    truth_text: str | None,
    truth_bbox: BBox | None,
    block: Block,
) -> float | None:
    if block.block_type is not truth_type:
        return None
    score = 0.5
    if truth_text is not None:
        if _normalized_text(block.text or "") != _normalized_text(truth_text):
            return None
        score += 0.3
    if truth_bbox is not None:
        score += 0.2 * _bbox_iou(truth_bbox, block.bbox)
    return score


def match_truth_blocks(document: DocumentIR, annotation: PageAnnotation) -> dict[str, Block]:
    """Match annotation blocks one-to-one without relying on parser order."""
    available = {
        str(block.block_id): block
        for block in _blocks_for_page(document, annotation.page_number)
    }
    matched: dict[str, Block] = {}
    for truth in sorted(annotation.layout_blocks, key=lambda item: str(item.truth_id)):
        candidates = [
            (score, block_id, block)
            for block_id, block in available.items()
            if (
                score := _block_compatibility(
                    truth.block_type, truth.text, truth.bbox, block
                )
            )
            is not None
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) <= _TIE_EPSILON:
            continue
        _, block_id, block = candidates[0]
        matched[str(truth.truth_id)] = block
        del available[block_id]
    return matched


def _table_text_counter(table: TableTruth | Table) -> Counter[str]:
    return Counter(
        normalized
        for cell in table.cells
        if (normalized := _normalized_text(cell.text))
    )


def _counter_f1(left: Counter[str], right: Counter[str]) -> float:
    overlap = sum((left & right).values())
    left_total = sum(left.values())
    right_total = sum(right.values())
    if left_total == 0 and right_total == 0:
        return 1.0
    precision = overlap / right_total if right_total else 0.0
    recall = overlap / left_total if left_total else 0.0
    return 0.0 if precision + recall == 0.0 else 2 * precision * recall / (precision + recall)


def _dimension_similarity(expected: int, actual: int) -> float:
    return min(expected, actual) / max(expected, actual)


def _table_bbox_similarity(truth: TableTruth, actual: Table) -> float:
    scores = [
        _bbox_iou(truth_segment.bbox, actual_segment.bbox)
        for truth_segment in truth.page_segments
        for actual_segment in actual.segments
        if truth_segment.page_number == actual_segment.page_number
        and truth_segment.bbox is not None
    ]
    return max(scores, default=0.0)


def _table_compatibility(truth: TableTruth, actual: Table) -> float | None:
    truth_pages = {segment.page_number for segment in truth.page_segments}
    actual_pages = {segment.page_number for segment in actual.segments}
    if not truth_pages & actual_pages:
        return None
    dimensions = (
        _dimension_similarity(truth.logical_rows, actual.logical_row_count)
        + _dimension_similarity(truth.logical_columns, actual.logical_column_count)
    ) / 2
    text = _counter_f1(_table_text_counter(truth), _table_text_counter(actual))
    bbox = _table_bbox_similarity(truth, actual)
    return 0.45 * dimensions + 0.45 * text + 0.10 * bbox


def _deterministic_table_matches(
    truths: Sequence[TableTruth], predictions: Sequence[Table]
) -> dict[str, Table]:
    remaining_truth = {str(table.truth_table_id): table for table in truths}
    remaining_predictions = {str(table.table_id): table for table in predictions}
    scores = {
        (truth_id, prediction_id): score
        for truth_id, truth in remaining_truth.items()
        for prediction_id, prediction in remaining_predictions.items()
        if (score := _table_compatibility(truth, prediction)) is not None
        and score >= TABLE_MATCH_MINIMUM
    }
    matches: dict[str, Table] = {}
    while remaining_truth and remaining_predictions:
        truth_choice: dict[str, str] = {}
        for truth_id in sorted(remaining_truth):
            candidates = sorted(
                (
                    (score, prediction_id)
                    for (candidate_truth, prediction_id), score in scores.items()
                    if candidate_truth == truth_id and prediction_id in remaining_predictions
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if candidates and not (
                len(candidates) > 1
                and abs(candidates[0][0] - candidates[1][0]) <= _TIE_EPSILON
            ):
                truth_choice[truth_id] = candidates[0][1]
        prediction_choice: dict[str, str] = {}
        for prediction_id in sorted(remaining_predictions):
            candidates = sorted(
                (
                    (score, truth_id)
                    for (truth_id, candidate_prediction), score in scores.items()
                    if candidate_prediction == prediction_id and truth_id in remaining_truth
                ),
                key=lambda item: (-item[0], item[1]),
            )
            if candidates and not (
                len(candidates) > 1
                and abs(candidates[0][0] - candidates[1][0]) <= _TIE_EPSILON
            ):
                prediction_choice[prediction_id] = candidates[0][1]
        mutual = sorted(
            (
                (scores[(truth_id, prediction_id)], truth_id, prediction_id)
                for truth_id, prediction_id in truth_choice.items()
                if prediction_choice.get(prediction_id) == truth_id
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        if not mutual:
            break
        for _, truth_id, prediction_id in mutual:
            if truth_id not in remaining_truth or prediction_id not in remaining_predictions:
                continue
            matches[truth_id] = remaining_predictions.pop(prediction_id)
            del remaining_truth[truth_id]
    return matches


def _occupied_grid_is_valid(table: Table) -> bool:
    occupied: set[tuple[int, int]] = set()
    for cell in table.cells:
        if (
            cell.row_index + cell.row_span > table.logical_row_count
            or cell.column_index + cell.column_span > table.logical_column_count
        ):
            return False
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            for column in range(cell.column_index, cell.column_index + cell.column_span):
                position = (row, column)
                if position in occupied:
                    return False
                occupied.add(position)
    return True


def _has_resolved_continuation(table: Table) -> bool:
    if len(table.segments) < 2:
        return False
    segments = sorted(table.segments, key=lambda item: (item.row_start, item.page_number))
    if segments[0].continued_from_segment_id is not None:
        return False
    if segments[-1].continues_to_segment_id is not None:
        return False
    return all(
        current.continues_to_segment_id == following.segment_id
        and following.continued_from_segment_id == current.segment_id
        for current, following in pairwise(segments)
    )


def score_table_predictions(
    truths: Sequence[TableTruth], predictions: Sequence[Table]
) -> TableScore:
    """Score already scoped project tables after deterministic identity matching."""

    matches = _deterministic_table_matches(truths, predictions)
    logical_rows_correct = logical_columns_correct = 0
    cells_text_correct = rowspans_correct = colspans_correct = 0
    unexpected_cells = occupied_grids_valid = 0
    segments_covered = continuations_correct = continuations_expected = 0
    matched_prediction_ids = {str(table.table_id) for table in matches.values()}
    truth_by_id = {str(table.truth_table_id): table for table in truths}
    for truth_id, truth in truth_by_id.items():
        prediction = matches.get(truth_id)
        if prediction is None:
            continue
        logical_rows_correct += int(truth.logical_rows == prediction.logical_row_count)
        logical_columns_correct += int(truth.logical_columns == prediction.logical_column_count)
        occupied_grids_valid += int(_occupied_grid_is_valid(prediction))
        expected_by_anchor = {
            (cell.row_index, cell.column_index): cell for cell in truth.cells
        }
        predicted_by_anchor = {
            (cell.row_index, cell.column_index): cell for cell in prediction.cells
        }
        for anchor, expected in expected_by_anchor.items():
            actual = predicted_by_anchor.get(anchor)
            if actual is None:
                continue
            cells_text_correct += int(
                _normalized_text(expected.text) == _normalized_text(actual.text)
            )
            rowspans_correct += int(expected.row_span == actual.row_span)
            colspans_correct += int(expected.column_span == actual.column_span)
        unexpected_cells += len(set(predicted_by_anchor) - set(expected_by_anchor))
        truth_pages = Counter(segment.page_number for segment in truth.page_segments)
        prediction_pages = Counter(segment.page_number for segment in prediction.segments)
        segments_covered += sum((truth_pages & prediction_pages).values())
        if len(truth.page_segments) > 1:
            continuations_expected += 1
            continuations_correct += int(
                truth_pages == prediction_pages and _has_resolved_continuation(prediction)
            )
    unexpected_cells += sum(
        len(table.cells)
        for table in predictions
        if str(table.table_id) not in matched_prediction_ids
    )
    return TableScore(
        matches=matches,
        detection_tp=len(matches),
        detection_fp=len(predictions) - len(matches),
        detection_fn=len(truths) - len(matches),
        logical_rows_correct=logical_rows_correct,
        logical_rows_expected=len(truths),
        logical_columns_correct=logical_columns_correct,
        logical_columns_expected=len(truths),
        cells_text_correct=cells_text_correct,
        cells_expected=sum(len(table.cells) for table in truths),
        unexpected_cells=unexpected_cells,
        rowspans_correct=rowspans_correct,
        colspans_correct=colspans_correct,
        occupied_grids_valid=occupied_grids_valid,
        occupied_grids_expected=len(truths),
        segments_covered=segments_covered,
        segments_expected=sum(len(table.page_segments) for table in truths),
        continuations_correct=continuations_correct,
        continuations_expected=continuations_expected,
    )


def _score_tables(document: DocumentIR, annotations: tuple[PageAnnotation, ...]) -> TableScore:
    truths = [table for annotation in annotations for table in annotation.tables]
    relevant_pages = {annotation.page_number for annotation in annotations}
    relevant_pages.update(
        segment.page_number for table in truths for segment in table.page_segments
    )
    predictions = [
        table
        for table in document.tables
        if any(segment.page_number in relevant_pages for segment in table.segments)
    ]
    return score_table_predictions(truths, predictions)


def assemble_page_text(document: DocumentIR, page_number: int) -> str:
    """Render the versioned project text view used by text and numeric metrics."""

    table_by_id = {str(table.table_id): table for table in document.tables}
    blocks = sorted(
        _blocks_for_page(document, page_number),
        key=lambda block: (
            block.reading_order
            if block.reading_order_status is ReadingOrderStatus.IN_FLOW
            and block.reading_order is not None
            else 10**9,
            str(block.block_id),
        ),
    )
    rendered_tables: set[str] = set()
    parts: list[str] = []
    for block in blocks:
        table = table_by_id.get(str(block.content_ref)) if block.content_ref is not None else None
        if block.block_type is BlockType.TABLE and table is not None:
            parts.extend(
                cell.text
                for cell in sorted(
                    table.cells,
                    key=lambda item: (item.row_index, item.column_index, str(item.cell_id)),
                )
                if cell.page_number == page_number
            )
            rendered_tables.add(str(table.table_id))
        elif block.text:
            parts.append(block.text)
    parts.extend(
        cell.text
        for table in document.tables
        if str(table.table_id) not in rendered_tables
        for cell in sorted(
            table.cells,
            key=lambda item: (
                item.page_number,
                item.row_index,
                item.column_index,
                str(item.cell_id),
            ),
        )
        if cell.page_number == page_number
    )
    return "\n".join(parts)


def _truth_cell(
    table: TableTruth,
    numeric_cell_id: str | None,
    row: int | None,
    column: int | None,
) -> TableCellTruth | None:
    if numeric_cell_id is not None:
        return next((cell for cell in table.cells if str(cell.cell_id) == numeric_cell_id), None)
    if row is None or column is None:
        return None
    return next(
        (cell for cell in table.cells if cell.row_index == row and cell.column_index == column),
        None,
    )


def _actual_cell(table: Table, truth_cell: TableCellTruth) -> TableCell | None:
    return next(
        (
            cell
            for cell in table.cells
            if cell.row_index == truth_cell.row_index
            and cell.column_index == truth_cell.column_index
        ),
        None,
    )


def evaluation_denominators(annotations: tuple[PageAnnotation, ...]) -> EvaluationDenominators:
    return EvaluationDenominators(
        pages=len(annotations),
        text_pages=sum(annotation.text is not None for annotation in annotations),
        reading_order_pairs=sum(
            len(annotation.reading_order_pairs) for annotation in annotations
        ),
        tables=sum(len(annotation.tables) for annotation in annotations),
        cells=sum(
            len(table.cells) for annotation in annotations for table in annotation.tables
        ),
        numeric_annotations=sum(
            numeric.multiplicity
            for annotation in annotations
            for numeric in annotation.critical_numerics
        ),
        structural_numeric_annotations=sum(
            numeric.multiplicity
            for annotation in annotations
            for numeric in annotation.critical_numerics
            if numeric.table_id is not None
        ),
    )


def score_numeric_predictions(
    document: DocumentIR,
    annotations: tuple[PageAnnotation, ...],
    table_score: TableScore,
) -> tuple[int, int, int, int]:
    """Return page-presence and structural numeric correct/expected counts."""
    page_correct = page_expected = 0
    structural_correct = structural_expected = 0
    for annotation in annotations:
        expected_page = Counter[str]()
        for numeric in annotation.critical_numerics:
            tokens = extract_numeric_tokens(numeric.value)
            if tokens:
                expected_page[tokens[0].normalized] += numeric.multiplicity
        actual_page = Counter(
            token.normalized
            for token in extract_numeric_tokens(
                assemble_page_text(document, annotation.page_number)
            )
        )
        page_correct += sum((expected_page & actual_page).values())
        page_expected += sum(expected_page.values())

        truth_tables = {str(table.truth_table_id): table for table in annotation.tables}
        consumed: Counter[tuple[str, int, int, str]] = Counter()
        for numeric in annotation.critical_numerics:
            if numeric.table_id is None:
                continue
            structural_expected += numeric.multiplicity
            truth_table = truth_tables.get(str(numeric.table_id))
            prediction = table_score.matches.get(str(numeric.table_id))
            if truth_table is None or prediction is None:
                continue
            truth_cell = _truth_cell(
                truth_table,
                str(numeric.cell_id) if numeric.cell_id is not None else None,
                numeric.row_index,
                numeric.column_index,
            )
            if truth_cell is None:
                continue
            actual = _actual_cell(prediction, truth_cell)
            tokens = extract_numeric_tokens(numeric.value)
            if actual is None or not tokens:
                continue
            normalized = tokens[0].normalized
            actual_counter = Counter(
                token.normalized for token in extract_numeric_tokens(actual.text)
            )
            key = (str(prediction.table_id), actual.row_index, actual.column_index, normalized)
            available = max(0, actual_counter[normalized] - consumed[key])
            metadata_matches = (
                (numeric.currency is None or str(numeric.currency) in actual.text)
                and (numeric.unit is None or str(numeric.unit) in actual.text)
            )
            hits = min(numeric.multiplicity, available) if metadata_matches else 0
            structural_correct += hits
            consumed[key] += hits
    return page_correct, page_expected, structural_correct, structural_expected


def _structure_readiness(document: DocumentIR) -> tuple[int, int, int, int]:
    eligible = [
        block
        for page in document.pages
        for block in page.blocks
        if block.block_type in _RETRIEVAL_BLOCK_TYPES
    ]
    assigned_ids = {
        block_id
        for section in document.sections
        for block_id in (
            *((section.heading_block_id,) if section.heading_block_id is not None else ()),
            *section.content_block_ids,
        )
    }
    assigned = sum(block.block_id in assigned_ids for block in eligible)
    eligible_pages = [
        page
        for page in document.pages
        if any(block.block_type in _RETRIEVAL_BLOCK_TYPES for block in page.blocks)
    ]
    resolved_pages = sum(
        all(
            block.reading_order_status is ReadingOrderStatus.IN_FLOW
            for block in page.blocks
            if block.block_type in _RETRIEVAL_BLOCK_TYPES
        )
        for page in eligible_pages
    )
    return len(eligible), assigned, len(eligible_pages), resolved_pages


def score_outcome(outcome: ParseOutcome, annotations: tuple[PageAnnotation, ...]) -> MetricValues:
    document = outcome.document
    annotated_pages = {annotation.page_number for annotation in annotations}
    present = {page.page_number for page in document.pages}
    text_scores: list[float] = []
    text_scored_characters = 0
    incomplete_reasons: list[str] = []
    reading_correct = reading_expected = 0
    for annotation in annotations:
        if annotation.text is not None:
            actual_text = assemble_page_text(document, annotation.page_number)
            text_result = compute_normalized_edit_similarity(
                annotation.text.expected_text, actual_text
            )
            if text_result.status is MetricStatus.COMPLETE:
                assert text_result.similarity is not None
                text_scores.append(text_result.similarity)
                text_scored_characters += text_result.scored_characters
            else:
                incomplete_reasons.append(
                    f"page {annotation.page_number}:{text_result.incomplete_reason}"
                )
        matched = match_truth_blocks(document, annotation)
        order = [
            truth_id
            for truth_id, block in sorted(
                matched.items(),
                key=lambda item: (
                    item[1].reading_order if item[1].reading_order is not None else 10**9,
                    str(item[1].block_id),
                ),
            )
            if block.reading_order_status is ReadingOrderStatus.IN_FLOW
        ]
        positions = {truth_id: index for index, truth_id in enumerate(order)}
        pairs = [
            (str(pair.before_truth_id), str(pair.after_truth_id))
            for pair in annotation.reading_order_pairs
        ]
        reading_expected += len(pairs)
        reading_correct += sum(
            before in positions and after in positions and positions[before] < positions[after]
            for before, after in pairs
        )
    if incomplete_reasons:
        text_status = MetricStatus.INCOMPLETE
        text_similarity = None
    elif text_scores:
        text_status = MetricStatus.COMPLETE
        text_similarity = sum(text_scores) / len(text_scores)
    else:
        text_status = MetricStatus.NOT_APPLICABLE
        text_similarity = None

    table_score = _score_tables(document, annotations)
    page_numeric_correct, page_numeric_expected, structural_correct, structural_expected = (
        score_numeric_predictions(document, annotations, table_score)
    )
    eligible_blocks, assigned_blocks, order_pages, resolved_pages = _structure_readiness(document)
    diagnostics = outcome.diagnostics
    precision = _ratio(
        table_score.detection_tp,
        table_score.detection_tp + table_score.detection_fp,
    )
    recall = _ratio(
        table_score.detection_tp,
        table_score.detection_tp + table_score.detection_fn,
    )
    return MetricValues(
        pages_expected=len(annotated_pages),
        pages_present=len(annotated_pages & present),
        page_completeness=len(annotated_pages & present) / max(len(annotated_pages), 1),
        text_metric_status=text_status,
        text_edit_similarity=text_similarity,
        text_pages_expected=sum(annotation.text is not None for annotation in annotations),
        text_pages_scored=len(text_scores),
        text_scored_characters=text_scored_characters,
        text_incomplete_reason=";".join(incomplete_reasons) or None,
        reading_order_pairs_correct=reading_correct,
        reading_order_pairs_expected=reading_expected,
        reading_order_pair_accuracy=_ratio(reading_correct, reading_expected),
        table_detection_tp=table_score.detection_tp,
        table_detection_fp=table_score.detection_fp,
        table_detection_fn=table_score.detection_fn,
        table_detection_precision=precision,
        table_detection_recall=recall,
        table_detection_f1=_f1(precision, recall),
        logical_rows_correct=table_score.logical_rows_correct,
        logical_rows_expected=table_score.logical_rows_expected,
        logical_row_accuracy=_ratio(
            table_score.logical_rows_correct, table_score.logical_rows_expected
        ),
        logical_columns_correct=table_score.logical_columns_correct,
        logical_columns_expected=table_score.logical_columns_expected,
        logical_column_accuracy=_ratio(
            table_score.logical_columns_correct, table_score.logical_columns_expected
        ),
        cells_text_correct=table_score.cells_text_correct,
        cells_expected=table_score.cells_expected,
        unexpected_cells=table_score.unexpected_cells,
        cell_exact_text_accuracy=_ratio(
            table_score.cells_text_correct, table_score.cells_expected
        ),
        rowspans_correct=table_score.rowspans_correct,
        rowspans_expected=table_score.cells_expected,
        rowspan_accuracy=_ratio(table_score.rowspans_correct, table_score.cells_expected),
        colspans_correct=table_score.colspans_correct,
        colspans_expected=table_score.cells_expected,
        colspan_accuracy=_ratio(table_score.colspans_correct, table_score.cells_expected),
        occupied_grids_valid=table_score.occupied_grids_valid,
        occupied_grids_expected=table_score.occupied_grids_expected,
        occupied_grid_validity=_ratio(
            table_score.occupied_grids_valid, table_score.occupied_grids_expected
        ),
        table_segments_covered=table_score.segments_covered,
        table_segments_expected=table_score.segments_expected,
        table_segment_coverage=_ratio(
            table_score.segments_covered, table_score.segments_expected
        ),
        continuations_correct=table_score.continuations_correct,
        continuations_expected=table_score.continuations_expected,
        continuation_identity_accuracy=_ratio(
            table_score.continuations_correct, table_score.continuations_expected
        ),
        page_numeric_presence_correct=page_numeric_correct,
        page_numeric_presence_expected=page_numeric_expected,
        page_numeric_presence_accuracy=_ratio(page_numeric_correct, page_numeric_expected),
        structural_numerics_correct=structural_correct,
        structural_numerics_expected=structural_expected,
        critical_numeric_structural_exact_accuracy=_ratio(
            structural_correct, structural_expected
        ),
        resolvable_block_provenance=(
            diagnostics.provenance_complete_blocks / max(diagnostics.generated_blocks, 1)
        ),
        exact_region_provenance=diagnostics.table_cells_with_exact_bbox,
        parent_region_provenance=diagnostics.table_cells_without_bbox,
        page_only_provenance=0,
        eligible_retrieval_blocks=eligible_blocks,
        section_assigned_blocks=assigned_blocks,
        section_assignment_coverage=_ratio(assigned_blocks, eligible_blocks),
        reading_order_pages_expected=order_pages,
        reading_order_pages_resolved=resolved_pages,
        resolved_reading_order_page_rate=_ratio(resolved_pages, order_pages),
        elapsed_seconds=diagnostics.elapsed_seconds,
        pages_per_second=(
            diagnostics.pages_parsed / diagnostics.elapsed_seconds
            if diagnostics.elapsed_seconds > 0
            else 0.0
        ),
        runtime_device=diagnostics.device.value,
    )

"""Deterministic Phase 2.6 parsing metrics; no aggregate parser score."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from docparser.application.parsing import ParseOutcome
from docparser.evaluation.models import MetricValues, PageAnnotation
from docparser.ir.models import Block, DocumentIR
from docparser.preflight import extract_numeric_tokens


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


def normalized_edit_similarity(expected: str, actual: str) -> float:
    left = _normalized_text(expected)
    right = _normalized_text(actual)
    denominator = max(len(left), len(right), 1)
    return max(0.0, 1.0 - _edit_distance(left, right) / denominator)


def pairwise_order_accuracy(order: Sequence[str], pairs: Sequence[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    positions = {value: index for index, value in enumerate(order)}
    return sum(
        before in positions and after in positions and positions[before] < positions[after]
        for before, after in pairs
    ) / len(pairs)


def _blocks_for_page(document: DocumentIR, page_number: int) -> tuple[Block, ...]:
    return document.pages[page_number - 1].blocks


def _match_truth_blocks(
    document: DocumentIR, annotation: PageAnnotation
) -> dict[str, Block]:
    available = list(_blocks_for_page(document, annotation.page_number))
    matched: dict[str, Block] = {}
    for truth in annotation.layout_blocks:
        candidates = [
            block
            for block in available
            if block.block_type is truth.block_type
            and (
                truth.text is None
                or _normalized_text(block.text or "") == _normalized_text(truth.text)
            )
        ]
        if candidates:
            block = candidates[0]
            matched[str(truth.truth_id)] = block
            available.remove(block)
    return matched


def _table_metrics(document: DocumentIR, annotations: tuple[PageAnnotation, ...]) -> tuple[
    int, int, float | None, float | None, float | None, float | None, float | None
]:
    truth_tables = [table for page in annotations for table in page.tables]
    actual_tables = [
        table
        for table in document.tables
        if any(page.page_number == table.segments[0].page_number for page in annotations)
    ]
    if not truth_tables:
        return 0, len(actual_tables), None, None, None, None, None
    compared = list(zip(truth_tables, actual_tables, strict=False))
    row = sum(expected.logical_rows == actual.logical_row_count for expected, actual in compared)
    column = sum(
        expected.logical_columns == actual.logical_column_count for expected, actual in compared
    )
    expected_cells = [cell for table in truth_tables for cell in table.cells]
    actual_cells = [cell for table in actual_tables for cell in table.cells]
    actual_by_position = {
        (cell.row_index, cell.column_index): cell for cell in actual_cells
    }
    text_hits = span_row_hits = span_column_hits = 0
    for expected in expected_cells:
        actual = actual_by_position.get((expected.row_index, expected.column_index))
        if actual is None:
            continue
        text_hits += _normalized_text(actual.text) == _normalized_text(expected.text)
        span_row_hits += actual.row_span == expected.row_span
        span_column_hits += actual.column_span == expected.column_span
    denominator = max(len(expected_cells), 1)
    table_denominator = max(len(truth_tables), 1)
    return (
        len(truth_tables),
        len(actual_tables),
        row / table_denominator,
        column / table_denominator,
        text_hits / denominator,
        span_row_hits / denominator,
        span_column_hits / denominator,
    )


def score_outcome(outcome: ParseOutcome, annotations: tuple[PageAnnotation, ...]) -> MetricValues:
    document = outcome.document
    annotated_pages = {annotation.page_number for annotation in annotations}
    present = {page.page_number for page in document.pages}
    text_scores: list[float] = []
    order_scores: list[float] = []
    numeric_expected = numeric_hits = 0
    for annotation in annotations:
        blocks = _blocks_for_page(document, annotation.page_number)
        if annotation.text is not None:
            actual_text = "\n".join(block.text or "" for block in blocks)
            text_scores.append(
                normalized_edit_similarity(annotation.text.expected_text, actual_text)
            )
        matched = _match_truth_blocks(document, annotation)
        order = [
            truth_id
            for truth_id, block in sorted(
                matched.items(),
                key=lambda item: (
                    item[1].reading_order
                    if item[1].reading_order is not None
                    else 10**9
                ),
            )
        ]
        pairs = [
            (str(pair.before_truth_id), str(pair.after_truth_id))
            for pair in annotation.reading_order_pairs
        ]
        if (score := pairwise_order_accuracy(order, pairs)) is not None:
            order_scores.append(score)
        page_text = "\n".join(block.text or "" for block in blocks)
        page_text += "\n" + "\n".join(
            cell.text
            for table in document.tables
            for cell in table.cells
            if cell.page_number == annotation.page_number
        )
        actual_numbers = {token.normalized for token in extract_numeric_tokens(page_text)}
        for truth in annotation.critical_numerics:
            expected = extract_numeric_tokens(truth.value)
            numeric_expected += 1
            numeric_hits += bool(expected and expected[0].normalized in actual_numbers)
    (
        expected_tables,
        actual_tables,
        row_accuracy,
        column_accuracy,
        cell_accuracy,
        rowspan_accuracy,
        colspan_accuracy,
    ) = _table_metrics(document, annotations)
    diagnostics = outcome.diagnostics
    return MetricValues(
        page_completeness=len(annotated_pages & present) / max(len(annotated_pages), 1),
        text_edit_similarity=sum(text_scores) / len(text_scores) if text_scores else None,
        reading_order_pair_accuracy=(
            sum(order_scores) / len(order_scores) if order_scores else None
        ),
        table_detection_count_expected=expected_tables,
        table_detection_count_actual=actual_tables,
        logical_row_accuracy=row_accuracy,
        logical_column_accuracy=column_accuracy,
        cell_exact_text_accuracy=cell_accuracy,
        rowspan_accuracy=rowspan_accuracy,
        colspan_accuracy=colspan_accuracy,
        critical_numeric_exact_accuracy=(
            numeric_hits / numeric_expected if numeric_expected else None
        ),
        resolvable_block_provenance=(
            diagnostics.provenance_complete_blocks / max(diagnostics.generated_blocks, 1)
        ),
        exact_region_provenance=diagnostics.table_cells_with_exact_bbox,
        parent_region_provenance=diagnostics.table_cells_without_bbox,
        page_only_provenance=0,
        elapsed_seconds=diagnostics.elapsed_seconds,
        pages_per_second=(
            diagnostics.pages_parsed / diagnostics.elapsed_seconds
            if diagnostics.elapsed_seconds > 0 else 0.0
        ),
        runtime_device=diagnostics.device.value,
    )

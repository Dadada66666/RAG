from __future__ import annotations

from uuid import UUID

from tests.parser_fixture import normalize_contract_fixture

from docparser.evaluation.metrics import (
    TEXT_ASSEMBLY_PROFILE,
    assemble_page_text,
    compute_normalized_edit_similarity,
    match_truth_blocks,
    normalized_edit_similarity,
    pairwise_order_accuracy,
    score_numeric_predictions,
    score_table_predictions,
)
from docparser.evaluation.models import (
    CriticalNumericTruth,
    LayoutBlockTruth,
    MetricStatus,
    PageAnnotation,
    TableCellTruth,
    TableSegmentTruth,
    TableTruth,
)
from docparser.ir.ids import TableId, TableSegmentId, generate_uuid5_id
from docparser.ir.tables import Table

_NAMESPACE = UUID("ac3cc35c-4a6d-58e2-91c1-a5ac1928eb37")


def _truth(table: Table, truth_id: str) -> TableTruth:
    return TableTruth(
        truth_table_id=truth_id,
        logical_table_id=f"logical-{truth_id}",
        logical_rows=table.logical_row_count,
        logical_columns=table.logical_column_count,
        page_segments=tuple(
            TableSegmentTruth(page_number=segment.page_number, bbox=segment.bbox)
            for segment in table.segments
        ),
        cells=tuple(
            TableCellTruth(
                cell_id=f"{truth_id}-cell-{index}",
                row_index=cell.row_index,
                column_index=cell.column_index,
                row_span=cell.row_span,
                column_span=cell.column_span,
                text=cell.text,
                is_header=cell.is_header,
                page_number=cell.page_number,
                bbox=cell.bbox,
            )
            for index, cell in enumerate(table.cells)
        ),
    )


def _with_table_id(table: Table, name: str) -> Table:
    return table.model_copy(
        update={"table_id": generate_uuid5_id(TableId, _NAMESPACE, name)}
    )


def test_text_metric_is_exact_for_nfc_equivalent_text() -> None:
    assert normalized_edit_similarity("café", "cafe\u0301") == 1.0
    assert normalized_edit_similarity("184,392.17", "184,392.71") < 1.0


def test_text_metric_reports_incomplete_instead_of_silent_truncation() -> None:
    result = compute_normalized_edit_similarity(
        "a" * 100,
        "b" * 100,
        max_matrix_cells=100,
    )

    assert result.status is MetricStatus.INCOMPLETE
    assert result.similarity is None
    assert result.scored_characters == 0
    assert result.incomplete_reason == "EDIT_DISTANCE_BUDGET_EXCEEDED:10201>100"


def test_text_assembly_profile_includes_logical_table_cells_once() -> None:
    document = normalize_contract_fixture("simple-table")

    assert TEXT_ASSEMBLY_PROFILE == "canonical-reading-flow-with-logical-tables@1.0.0"
    assert assemble_page_text(document, 1).splitlines() == [
        "Metric",
        "Value",
        "Revenue",
        "120",
    ]


def test_pairwise_reading_order_is_independent_metric() -> None:
    assert pairwise_order_accuracy(["a", "b", "c"], [("a", "b"), ("c", "b")]) == 0.5
    assert pairwise_order_accuracy([], []) is None


def test_table_matching_is_permutation_invariant_and_table_scoped() -> None:
    simple = _with_table_id(normalize_contract_fixture("simple-table").tables[0], "simple")
    merged = _with_table_id(normalize_contract_fixture("merged-table").tables[0], "merged")
    truths = (_truth(simple, "truth-simple"), _truth(merged, "truth-merged"))

    ordered = score_table_predictions(truths, (simple, merged))
    permuted = score_table_predictions(truths, (merged, simple))

    assert ordered == permuted
    assert ordered.detection_tp == 2
    assert ordered.cells_text_correct == len(simple.cells) + len(merged.cells)
    assert ordered.unexpected_cells == 0


def test_cells_with_same_anchor_do_not_collide_across_tables() -> None:
    first = _with_table_id(normalize_contract_fixture("simple-table").tables[0], "first")
    second = _with_table_id(normalize_contract_fixture("merged-table").tables[0], "second")

    score = score_table_predictions(
        (_truth(first, "truth-first"), _truth(second, "truth-second")),
        (first, second),
    )

    assert score.cells_expected == len(first.cells) + len(second.cells)
    assert score.cells_text_correct == score.cells_expected


def test_missing_and_extra_tables_are_explicit_detection_errors() -> None:
    simple = _with_table_id(normalize_contract_fixture("simple-table").tables[0], "simple")
    merged = _with_table_id(normalize_contract_fixture("merged-table").tables[0], "merged")
    unrelated = _with_table_id(simple, "unsupported-extra")

    missing = score_table_predictions(
        (_truth(simple, "truth-simple"), _truth(merged, "truth-merged")),
        (simple,),
    )
    extra = score_table_predictions((_truth(merged, "truth-merged"),), (merged, unrelated))

    assert (missing.detection_tp, missing.detection_fp, missing.detection_fn) == (1, 0, 1)
    assert (extra.detection_tp, extra.detection_fp, extra.detection_fn) == (1, 1, 0)
    assert extra.unexpected_cells == len(unrelated.cells)


def test_ambiguous_equal_table_candidates_remain_unmatched() -> None:
    first = _with_table_id(normalize_contract_fixture("simple-table").tables[0], "first")
    duplicate = _with_table_id(first, "duplicate")

    score = score_table_predictions((_truth(first, "truth-simple"),), (first, duplicate))

    assert (score.detection_tp, score.detection_fp, score.detection_fn) == (0, 2, 1)


def test_merged_cell_span_is_scored_independently() -> None:
    merged = _with_table_id(normalize_contract_fixture("merged-table").tables[0], "merged")
    wrong_anchor = merged.cells[0].model_copy(update={"column_span": 1})
    wrong = merged.model_copy(update={"cells": (wrong_anchor, *merged.cells[1:])})

    correct_score = score_table_predictions((_truth(merged, "truth-merged"),), (merged,))
    wrong_score = score_table_predictions((_truth(merged, "truth-merged"),), (wrong,))

    assert correct_score.colspans_correct == correct_score.cells_expected
    assert wrong_score.colspans_correct == wrong_score.cells_expected - 1


def test_cross_page_segment_coverage_and_continuation_are_independent() -> None:
    base = _with_table_id(normalize_contract_fixture("simple-table").tables[0], "cross-page")
    first_id = generate_uuid5_id(TableSegmentId, _NAMESPACE, "segment-1")
    second_id = generate_uuid5_id(TableSegmentId, _NAMESPACE, "segment-2")
    first = base.segments[0].model_copy(
        update={
            "segment_id": first_id,
            "row_start": 0,
            "row_end_exclusive": 1,
            "continues_to_segment_id": second_id,
        }
    )
    second = base.segments[0].model_copy(
        update={
            "segment_id": second_id,
            "page_number": 2,
            "row_start": 1,
            "row_end_exclusive": 2,
            "continued_from_segment_id": first_id,
            "continues_to_segment_id": None,
        }
    )
    resolved = base.model_copy(update={"segments": (first, second)})
    unresolved = base.model_copy(
        update={
            "segments": (
                first.model_copy(update={"continues_to_segment_id": None}),
            )
        }
    )
    truth = _truth(resolved, "truth-cross-page")

    resolved_score = score_table_predictions((truth,), (resolved,))
    unresolved_score = score_table_predictions((truth,), (unresolved,))

    assert (resolved_score.segments_covered, resolved_score.segments_expected) == (2, 2)
    assert (resolved_score.continuations_correct, resolved_score.continuations_expected) == (1, 1)
    assert (unresolved_score.segments_covered, unresolved_score.segments_expected) == (1, 2)
    assert (
        unresolved_score.continuations_correct,
        unresolved_score.continuations_expected,
    ) == (0, 1)


def test_numeric_presence_does_not_grant_wrong_cell_structural_credit() -> None:
    document = normalize_contract_fixture("simple-table")
    table_truth = _truth(document.tables[0], "truth-simple")
    annotation = PageAnnotation(
        page_number=1,
        tables=(table_truth,),
        critical_numerics=(
            CriticalNumericTruth(
                truth_id="revenue-in-wrong-cell",
                value="120",
                table_id="truth-simple",
                row_index=0,
                column_index=0,
            ),
        ),
    )
    table_score = score_table_predictions((table_truth,), document.tables)

    page_correct, page_expected, structural_correct, structural_expected = (
        score_numeric_predictions(document, (annotation,), table_score)
    )

    assert (page_correct, page_expected) == (1, 1)
    assert (structural_correct, structural_expected) == (0, 1)


def test_numeric_presence_preserves_multiplicity() -> None:
    document = normalize_contract_fixture("simple-table")
    annotation = PageAnnotation(
        page_number=1,
        critical_numerics=(
            CriticalNumericTruth(
                truth_id="revenue-twice",
                value="120",
                multiplicity=2,
            ),
        ),
    )

    page_correct, page_expected, structural_correct, structural_expected = (
        score_numeric_predictions(
            document,
            (annotation,),
            score_table_predictions((), ()),
        )
    )

    assert (page_correct, page_expected) == (1, 2)
    assert (structural_correct, structural_expected) == (0, 0)


def test_duplicate_reading_text_is_matched_by_geometry() -> None:
    document = normalize_contract_fixture("two-column")
    page = document.pages[0]
    left = page.blocks[1].model_copy(update={"text": "duplicate"})
    right = page.blocks[3].model_copy(update={"text": "duplicate"})
    modified_page = page.model_copy(update={"blocks": (left, right)})
    modified_document = document.model_copy(update={"pages": (modified_page,)})
    annotation = PageAnnotation(
        page_number=1,
        layout_blocks=(
            LayoutBlockTruth(
                truth_id="right",
                block_type=right.block_type,
                text="duplicate",
                bbox=right.bbox,
            ),
            LayoutBlockTruth(
                truth_id="left",
                block_type=left.block_type,
                text="duplicate",
                bbox=left.bbox,
            ),
        ),
    )

    matches = match_truth_blocks(modified_document, annotation)

    assert matches["left"].block_id == left.block_id
    assert matches["right"].block_id == right.block_id

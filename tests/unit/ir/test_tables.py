from __future__ import annotations

import json
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.full_ir_factory import make_full_document
from tests.ir_factory import TEST_NAMESPACE

from docparser.ir.enums import RelationshipType
from docparser.ir.ids import RelationshipId, generate_uuid5_id
from docparser.ir.serialization import dump_canonical_json, load_canonical_json
from docparser.ir.tables import Table


def _table_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(make_full_document().tables[0].model_dump_json()))


def test_cross_page_table_preserves_merged_cell_fragments() -> None:
    table = make_full_document().tables[0]
    merged = next(cell for cell in table.cells if cell.row_span == 2)

    assert [segment.page_number for segment in table.segments] == [1, 2]
    assert [fragment.page_number for fragment in merged.fragments] == [1, 2]
    assert merged.bbox is not None


def test_table_fragment_must_overlap_its_referenced_segment() -> None:
    payload = _table_payload()
    payload["cells"][2]["fragments"][0]["bbox"] = [560.0, 780.0, 580.0, 800.0]

    with pytest.raises(ValidationError, match="must overlap"):
        Table.model_validate_json(json.dumps(payload))


def test_simple_table_with_header_row_validates() -> None:
    payload = _table_payload()
    payload["logical_row_count"] = 1
    payload["segments"] = [payload["segments"][0]]
    payload["segments"][0]["row_end_exclusive"] = 1
    payload["segments"][0]["continues_to_segment_id"] = None
    payload["cells"] = payload["cells"][:2]

    table = Table.model_validate_json(json.dumps(payload))

    assert table.header_row_indices == (0,)


def test_overlapping_published_table_cells_are_rejected() -> None:
    payload = cast(dict[str, Any], json.loads(dump_canonical_json(make_full_document())))
    payload["tables"][0]["cells"][1]["row_index"] = 0
    payload["tables"][0]["cells"][1]["column_index"] = 0

    with pytest.raises(ValidationError, match="overlap"):
        load_canonical_json(json.dumps(payload))


def test_nonpublished_table_may_retain_explicit_alternative_cells() -> None:
    document = make_full_document()
    payload = cast(dict[str, Any], json.loads(dump_canonical_json(document)))
    first_cell = payload["tables"][0]["cells"][0]
    second_cell = payload["tables"][0]["cells"][1]
    second_cell["row_index"] = first_cell["row_index"]
    second_cell["column_index"] = first_cell["column_index"]
    payload["quality_summary"]["publishable"] = False
    payload["quality_summary"]["status"] = "DEGRADED"
    payload["relationships"].append(
        {
            "relationship_id": str(
                generate_uuid5_id(RelationshipId, TEST_NAMESPACE, "alternative-cells")
            ),
            "type": RelationshipType.ALTERNATIVE_TO.value,
            "source_id": first_cell["cell_id"],
            "target_id": second_cell["cell_id"],
            "confidence": None,
            "provenance_ids": [payload["provenance"][0]["provenance_id"]],
            "metadata": {},
            "extensions": {},
        }
    )

    assert load_canonical_json(json.dumps(payload)).quality_summary.publishable is False


def test_table_cell_span_outside_grid_is_rejected() -> None:
    payload = _table_payload()
    payload["cells"][0]["row_span"] = 4

    with pytest.raises(ValidationError, match="exceeds logical table dimensions"):
        Table.model_validate_json(json.dumps(payload))


@settings(max_examples=30)
@given(
    rows=st.integers(min_value=1, max_value=20),
    columns=st.integers(min_value=1, max_value=20),
    row=st.integers(min_value=0, max_value=19),
    column=st.integers(min_value=0, max_value=19),
    row_span=st.integers(min_value=1, max_value=8),
    column_span=st.integers(min_value=1, max_value=8),
)
def test_table_span_property(
    rows: int,
    columns: int,
    row: int,
    column: int,
    row_span: int,
    column_span: int,
) -> None:
    payload = _table_payload()
    payload["logical_row_count"] = rows
    payload["logical_column_count"] = columns
    segment = payload["segments"][0]
    segment["row_start"] = 0
    segment["row_end_exclusive"] = rows
    segment["continued_from_segment_id"] = None
    segment["continues_to_segment_id"] = None
    payload["segments"] = [segment]
    cell = payload["cells"][0]
    cell["row_index"] = row
    cell["column_index"] = column
    cell["row_span"] = row_span
    cell["column_span"] = column_span
    payload["cells"] = [cell]
    payload["header_row_indices"] = []

    fits = row + row_span <= rows and column + column_span <= columns
    if fits:
        Table.model_validate_json(json.dumps(payload))
    else:
        with pytest.raises(ValidationError, match="exceeds logical table dimensions"):
            Table.model_validate_json(json.dumps(payload))

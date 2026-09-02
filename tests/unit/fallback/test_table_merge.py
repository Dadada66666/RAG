from __future__ import annotations

from uuid import UUID

import pytest
from tests.fallback_factory import duplicate_table_on_page, with_alternate_run
from tests.full_ir_factory import make_full_document
from tests.parser_fixture import normalize_contract_fixture

from docparser.fallback import (
    CandidatePage,
    UnsupportedDependencyError,
    match_table_candidate,
    replace_table_atomic,
)
from docparser.ir.ids import RevisionId, TableId, generate_uuid5_id
from docparser.ir.types import UtcTimestamp


def test_atomic_table_replacement_leaves_second_table_unchanged() -> None:
    baseline = duplicate_table_on_page(normalize_contract_fixture("simple-table"))
    target = baseline.tables[0]
    untouched = baseline.tables[1]
    candidate_document = with_alternate_run(normalize_contract_fixture("simple-table"))
    candidate = CandidatePage(original_page_number=1, document=candidate_document)

    revised = replace_table_atomic(
        baseline,
        candidate,
        target,
        candidate_document.tables[0],
        attempt_fingerprint=f"sha256:{'e' * 64}",
        triggering_rule_ids=("TABLE.DEGENERATE_STRUCTURE",),
        revision_id_factory=lambda: RevisionId("rev_018bcfe5-6800-7000-8000-000000000089"),
        clock=lambda: UtcTimestamp("2026-09-02T08:02:00Z"),
    )

    assert revised.tables[1] == untouched
    assert revised.tables[0].table_id == target.table_id
    assert revised.tables[0].segments[0].block_id == target.segments[0].block_id
    replacement_provenance = [
        record
        for record in revised.provenance
        if record.operation and record.operation.startswith("FALLBACK_TABLE_REPLACE")
    ]
    assert replacement_provenance
    assert all(
        record.source_artifact_id == baseline.source.source_artifact_id
        for record in replacement_provenance
    )


def test_ambiguous_candidate_conflicts_instead_of_arbitrary_selection() -> None:
    baseline = normalize_contract_fixture("simple-table").tables[0]
    candidate = baseline.model_copy(
        update={
            "table_id": generate_uuid5_id(
                TableId,
                UUID("f87dcb3e-ec8c-5bb8-b5ef-ef013e17f18f"),
                "candidate-two",
            )
        }
    )

    match = match_table_candidate(
        baseline,
        (baseline, candidate),
        minimum_score=0.5,
        winner_margin=0.1,
    )

    assert match.status == "CONFLICT"
    assert match.candidate is None


def test_cross_page_table_replacement_is_explicitly_unsupported() -> None:
    baseline = make_full_document()
    candidate_document = with_alternate_run(normalize_contract_fixture("simple-table"))

    with pytest.raises(UnsupportedDependencyError, match="cross-page"):
        replace_table_atomic(
            baseline,
            CandidatePage(original_page_number=1, document=candidate_document),
            baseline.tables[0],
            candidate_document.tables[0],
            attempt_fingerprint=f"sha256:{'f' * 64}",
            triggering_rule_ids=("TABLE.DEGENERATE_STRUCTURE",),
        )

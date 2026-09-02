from __future__ import annotations

import pytest
from tests.fallback_factory import candidate_document, multi_page_document

from docparser.fallback import CandidatePage, replace_page_atomic
from docparser.ir.geometry import BBox
from docparser.ir.ids import RevisionId
from docparser.ir.types import Sha256Digest, UtcTimestamp


def test_page_replacement_changes_only_page_six_and_records_fallback_provenance() -> None:
    baseline = multi_page_document()
    candidate = CandidatePage(
        original_page_number=6,
        materialized_digest=Sha256Digest(f"sha256:{'a' * 64}"),
        document=candidate_document(),
    )

    revised = replace_page_atomic(
        baseline,
        candidate,
        attempt_fingerprint=f"sha256:{'d' * 64}",
        triggering_rule_ids=("ORDER.UNRESOLVED",),
        revision_id_factory=lambda: RevisionId("rev_018bcfe5-6800-7000-8000-000000000088"),
        clock=lambda: UtcTimestamp("2026-09-02T08:01:00Z"),
    )

    assert revised.revision_number == baseline.revision_number + 1
    assert revised.previous_revision_id == baseline.revision_id
    assert revised.pages[5].blocks[0].text == "candidate repaired page"
    assert all(revised.pages[index] == baseline.pages[index] for index in range(10) if index != 5)
    fallback_records = [
        record
        for record in revised.provenance
        if record.operation and record.operation.startswith("FALLBACK_PAGE_REPARSE")
    ]
    assert fallback_records
    assert all(
        record.source_artifact_id == baseline.source.source_artifact_id
        for record in fallback_records
    )
    assert all(record.page_number == 6 for record in fallback_records)
    assert all(record.parser_run_id is not None for record in fallback_records)
    assert revised.processing.parser_runs[-1].runtime["materialized_digest"] == (
        f"sha256:{'a' * 64}"
    )


def test_invalid_candidate_rolls_back_without_mutating_baseline() -> None:
    baseline = multi_page_document()
    candidate = candidate_document()
    invalid_block = candidate.pages[0].blocks[0].model_copy(
        update={"bbox": BBox((0.0, 0.0, 700.0, 900.0))}
    )
    invalid_page = candidate.pages[0].model_copy(update={"blocks": (invalid_block,)})
    invalid_candidate = candidate.model_copy(update={"pages": (invalid_page,)})
    original_revision = baseline.revision_id

    with pytest.raises(ValueError):
        replace_page_atomic(
            baseline,
            CandidatePage(
                original_page_number=6,
                materialized_digest=Sha256Digest(f"sha256:{'a' * 64}"),
                document=invalid_candidate,
            ),
            attempt_fingerprint=f"sha256:{'1' * 64}",
            triggering_rule_ids=("ORDER.UNRESOLVED",),
        )

    assert baseline.revision_id == original_revision
    assert baseline.pages[5].blocks[0].text == "baseline page 6"

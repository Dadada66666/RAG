from __future__ import annotations

import pytest
from pydantic import ValidationError

from docparser.fallback import FallbackProfile
from docparser.ir.types import Sha256Digest


def test_frozen_fallback_profile_requires_evidence_report_linkage() -> None:
    with pytest.raises(ValidationError, match="evidence report linkage"):
        FallbackProfile(
            profile_id="fallback-without-evidence",
            evidence_dataset_digest=Sha256Digest(f"sha256:{'c' * 64}"),
            created_from_commit="test-commit",
            primary_profile="docling-standard",
            alternate_profile="paddleocr-vl-1.6",
            supported_slice="test",
            eligible_rule_ids=("ORDER.UNRESOLVED",),
            minimum_candidate_match=0.5,
            winner_margin=0.1,
            frozen=True,
        )

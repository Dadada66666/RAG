import pytest
from pydantic import ValidationError

from docparser.ir.content import IssueCounts, QualitySummary
from docparser.ir.enums import QualityStatus


def test_not_evaluated_is_explicit_and_not_publishable() -> None:
    summary = QualitySummary(
        quality_report_id=None,
        score=None,
        status=QualityStatus.NOT_EVALUATED,
        issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
        publishable=False,
    )

    assert summary.score is None


def test_not_evaluated_cannot_fake_a_score() -> None:
    with pytest.raises(ValidationError, match="must not declare"):
        QualitySummary(
            quality_report_id=None,
            score=1.0,
            status=QualityStatus.NOT_EVALUATED,
            issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
            publishable=False,
        )


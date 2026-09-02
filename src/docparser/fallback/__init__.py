"""Selective PAGE/TABLE fallback public API."""

from docparser.fallback.matching import TableMatch, match_table_candidate
from docparser.fallback.materialize import materialize_page
from docparser.fallback.merge import (
    UnsupportedDependencyError,
    replace_page_atomic,
    replace_table_atomic,
)
from docparser.fallback.models import (
    CandidatePage,
    FallbackBudget,
    FallbackPlan,
    FallbackProfile,
    FallbackResult,
    FallbackTargetResult,
    FallbackTargetStatus,
    MaterializedPage,
    PlannedFallbackTarget,
    RobustDiagnostics,
    RobustParseOutcome,
)
from docparser.fallback.planner import build_fallback_plan

__all__ = [
    "CandidatePage",
    "FallbackBudget",
    "FallbackPlan",
    "FallbackProfile",
    "FallbackResult",
    "FallbackTargetResult",
    "FallbackTargetStatus",
    "MaterializedPage",
    "PlannedFallbackTarget",
    "RobustDiagnostics",
    "RobustParseOutcome",
    "TableMatch",
    "UnsupportedDependencyError",
    "build_fallback_plan",
    "match_table_candidate",
    "materialize_page",
    "replace_page_atomic",
    "replace_table_atomic",
]

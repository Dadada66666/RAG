"""Small evidence-driven Quality Gate rule set."""

from __future__ import annotations

from collections import Counter
from typing import Protocol, cast

from pydantic import JsonValue

from docparser.ir.enums import BlockType, ReadingOrderStatus
from docparser.ir.tables import Table
from docparser.preflight import NativeTextReliability, extract_numeric_tokens
from docparser.quality.models import (
    QualityMode,
    QualityScope,
    QualitySignal,
    QualityTarget,
    RuleAction,
    SignalKind,
    SignalOutcome,
    SignalSeverity,
    ValidationRequest,
    quality_mode_for_request,
)


class QualityRule(Protocol):
    rule_id: str

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]: ...


def _action(context: ValidationRequest, rule_id: str) -> tuple[RuleAction, bool]:
    profile = context.calibration
    mode = quality_mode_for_request(context)
    if profile is None or mode is QualityMode.OBSERVE_ONLY:
        return RuleAction.ADVISORY, False
    return (
        profile.rule_actions.get(rule_id, RuleAction.ADVISORY),
        mode is QualityMode.CALIBRATED,
    )


def _page_text(context: ValidationRequest, page_number: int) -> str:
    page = context.document.pages[page_number - 1]
    parts = [block.text or "" for block in page.blocks if block.block_type is not BlockType.TABLE]
    parts.extend(
        cell.text
        for table in context.document.tables
        for cell in table.cells
        if cell.page_number == page_number
    )
    return "\n".join(parts)


class SourceRichParseSparseRule:
    rule_id = "COMPLETENESS.SOURCE_RICH_PARSE_SPARSE"

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]:
        action, calibrated = _action(context, self.rule_id)
        thresholds = (
            context.calibration.completeness
            if context.calibration is not None
            and quality_mode_for_request(context) is not QualityMode.OBSERVE_ONLY
            else None
        )
        signals: list[QualitySignal] = []
        for source_page in context.profile.pages:
            native_chars = source_page.text_char_count
            parsed_chars = len(_page_text(context, source_page.page_number).strip())
            applicable = (
                source_page.native_text_evidence.reliability
                is NativeTextReliability.RELIABLE
                and native_chars > 0
            )
            ratio = parsed_chars / native_chars if applicable else None
            if not applicable:
                outcome = SignalOutcome.NOT_APPLICABLE
            elif thresholds is None:
                outcome = SignalOutcome.PROVISIONAL
            else:
                outcome = (
                    SignalOutcome.TRIGGERED
                    if native_chars >= thresholds.minimum_native_characters
                    and ratio is not None
                    and ratio < thresholds.minimum_parser_to_native_ratio
                    else SignalOutcome.CLEAR
                )
            signals.append(
                QualitySignal(
                    rule_id=self.rule_id,
                    signal_kind=SignalKind.ANOMALY,
                    severity=SignalSeverity.ERROR,
                    outcome=outcome,
                    target=QualityTarget(
                        scope=QualityScope.PAGE,
                        page_number=source_page.page_number,
                    ),
                    predicted_failure_type="CONTENT_COMPLETENESS_FAILURE",
                    action=action,
                    calibrated=calibrated and thresholds is not None,
                    evidence={
                        "native_characters": native_chars,
                        "parser_characters": parsed_chars,
                        "parser_to_native_ratio": ratio,
                        "likely_image_only": source_page.likely_image_only,
                        "text_extraction_status": source_page.text_extraction_status.value,
                    },
                    message="Native PDF evidence is source-rich but parsed content is sparse.",
                )
            )
        return tuple(signals)


class NumericDisagreementRule:
    rule_id = "NUMERIC.NATIVE_PARSER_DISAGREEMENT"

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]:
        action, calibrated = _action(context, self.rule_id)
        signals: list[QualitySignal] = []
        for source_page in context.profile.pages:
            applicable = (
                source_page.native_text_evidence.reliability
                is NativeTextReliability.RELIABLE
            )
            native = (
                Counter(
                    token.normalized
                    for token in source_page.native_text_evidence.normalized_numeric_tokens
                )
                if applicable
                else Counter()
            )
            parser = (
                Counter(
                    token.normalized
                    for token in extract_numeric_tokens(
                        _page_text(context, source_page.page_number)
                    )
                )
                if applicable
                else Counter()
            )
            missing = native - parser
            extra = parser - native
            outcome = (
                SignalOutcome.NOT_APPLICABLE
                if not applicable
                else SignalOutcome.TRIGGERED
                if missing or extra
                else SignalOutcome.CLEAR
            )
            signals.append(
                QualitySignal(
                    rule_id=self.rule_id,
                    signal_kind=SignalKind.DISAGREEMENT,
                    severity=SignalSeverity.ERROR,
                    outcome=outcome,
                    target=QualityTarget(
                        scope=QualityScope.PAGE,
                        page_number=source_page.page_number,
                    ),
                    predicted_failure_type="NUMERIC_DISAGREEMENT",
                    action=action,
                    calibrated=calibrated,
                    evidence={
                        "native_values": cast(list[JsonValue], sorted(native.elements())),
                        "parser_values": cast(list[JsonValue], sorted(parser.elements())),
                        "missing_native_values": cast(list[JsonValue], sorted(missing.elements())),
                        "extra_parser_values": cast(list[JsonValue], sorted(extra.elements())),
                    },
                    message=(
                        "Native PDF and parser numeric multisets disagree; neither source is "
                        "assumed correct."
                    ),
                )
            )
        return tuple(signals)


class UnresolvedReadingOrderRule:
    rule_id = "ORDER.UNRESOLVED"

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]:
        action, calibrated = _action(context, self.rule_id)
        signals: list[QualitySignal] = []
        for page in context.document.pages:
            unresolved = sum(
                block.reading_order_status is ReadingOrderStatus.UNRESOLVED for block in page.blocks
            )
            signals.append(
                QualitySignal(
                    rule_id=self.rule_id,
                    signal_kind=SignalKind.UNCERTAINTY,
                    severity=SignalSeverity.WARNING,
                    outcome=SignalOutcome.TRIGGERED if unresolved else SignalOutcome.CLEAR,
                    target=QualityTarget(scope=QualityScope.PAGE, page_number=page.page_number),
                    predicted_failure_type="READING_ORDER_UNRESOLVED",
                    action=action,
                    calibrated=calibrated,
                    evidence={"unresolved_blocks": unresolved},
                    message="Parser evidence does not resolve reading order for all page blocks.",
                )
            )
        return tuple(signals)


def _occupied_grid_ratio(table: Table) -> float:
    occupied = sum(cell.row_span * cell.column_span for cell in table.cells)
    total = table.logical_row_count * table.logical_column_count
    return min(1.0, occupied / total)


class DegenerateTableRule:
    rule_id = "TABLE.DEGENERATE_STRUCTURE"

    def evaluate(self, context: ValidationRequest) -> tuple[QualitySignal, ...]:
        action, calibrated = _action(context, self.rule_id)
        thresholds = (
            context.calibration.table
            if context.calibration is not None
            and quality_mode_for_request(context) is not QualityMode.OBSERVE_ONLY
            else None
        )
        signals: list[QualitySignal] = []
        for table in context.document.tables:
            empty_ratio = sum(not cell.text.strip() for cell in table.cells) / len(table.cells)
            occupied_ratio = _occupied_grid_ratio(table)
            outcome = (
                SignalOutcome.PROVISIONAL
                if thresholds is None
                else SignalOutcome.TRIGGERED
                if empty_ratio > thresholds.maximum_empty_cell_ratio
                or occupied_ratio < thresholds.minimum_occupied_grid_ratio
                else SignalOutcome.CLEAR
            )
            signals.append(
                QualitySignal(
                    rule_id=self.rule_id,
                    signal_kind=SignalKind.ANOMALY,
                    severity=SignalSeverity.ERROR,
                    outcome=outcome,
                    target=QualityTarget(
                        scope=QualityScope.TABLE,
                        page_number=table.segments[0].page_number,
                        table_id=table.table_id,
                    ),
                    predicted_failure_type="TABLE_STRUCTURE_FAILURE",
                    action=action,
                    calibrated=calibrated and thresholds is not None,
                    evidence={
                        "row_count": table.logical_row_count,
                        "column_count": table.logical_column_count,
                        "cell_count": len(table.cells),
                        "empty_cell_ratio": empty_ratio,
                        "occupied_grid_ratio": occupied_ratio,
                        "cell_bbox_count": sum(cell.bbox is not None for cell in table.cells),
                        "numeric_token_count": sum(
                            len(extract_numeric_tokens(cell.text)) for cell in table.cells
                        ),
                    },
                    message="Table structure is sparse or dominated by empty cells.",
                )
            )
        return tuple(signals)


DEFAULT_RULES: tuple[QualityRule, ...] = (
    SourceRichParseSparseRule(),
    NumericDisagreementRule(),
    UnresolvedReadingOrderRule(),
    DegenerateTableRule(),
)

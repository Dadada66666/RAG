"""Local recovery of known structural failures without inventing document content."""

from __future__ import annotations

from dataclasses import dataclass

from docparser.domain.parser_contract import (
    ExtractedElement,
    ExtractedElementType,
    ExtractedTable,
    PageParseResult,
    ParseResult,
)
from docparser.ir.models import DocumentIR
from docparser.normalization.base import NormalizationContext, NormalizationError
from docparser.normalization.neutral import normalize_neutral_result

RECOVERY_KEY = "org.docparser.recovery"
RECOVERY_VERSION = "local-structure-recovery@1.0.0"


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    document: DocumentIR
    warnings: tuple[str, ...]


def _table_failure(table: ExtractedTable) -> str | None:
    """Check the same dimensions/occupancy required by the canonical table grid."""
    occupied: set[tuple[int, int]] = set()
    for cell in table.cells:
        row_end = cell.row_index + cell.row_span
        column_end = cell.column_index + cell.column_span
        if row_end > table.row_count or column_end > table.column_count:
            return "TABLE_SPAN_OUT_OF_BOUNDS"
        for row in range(cell.row_index, row_end):
            for column in range(cell.column_index, column_end):
                position = (row, column)
                if position in occupied:
                    return "TABLE_GRID_OVERLAP"
                occupied.add(position)
    return None


def _recover_text(element: ExtractedElement, reason: str, text: str) -> ExtractedElement:
    return element.model_copy(
        update={
            "element_type": ExtractedElementType.UNKNOWN,
            "text": text,
            "reading_order": None,
            "reading_order_resolved": False,
            "metadata": {
                **element.metadata,
                RECOVERY_KEY: {
                    "reason": reason,
                    "original_type": element.element_type.value,
                    "retrievable_text": bool(text.strip()) and not element.decorative,
                    "structure_usable": False,
                },
            },
        }
    )


def normalize_recoverable_result(
    result: ParseResult, context: NormalizationContext
) -> RecoveryOutcome:
    """Retain partial evidence; all successful canonical entities still pass strict validation.

    Identity errors, invalid page attribution and coordinate errors still fail normalization.
    This function only downgrades the specific structural failures diagnosed below.
    The caller retains the original result, including the rejected table observations.
    """
    expected = tuple(range(1, context.profile.page_count + 1))
    if result.pages_requested != expected:
        raise NormalizationError("recovery requires a document-scoped parse of the source pages")
    actual = {page.page_number: page for page in result.pages}
    pages: list[PageParseResult] = []
    warnings: list[str] = []
    missing: list[int] = []
    degraded = 0
    for profile in context.profile.pages:
        page = actual.get(profile.page_number)
        if page is None:
            missing.append(profile.page_number)
            warnings.append(f"page {profile.page_number}: MISSING_PAGE; no text was fabricated")
            pages.append(
                PageParseResult(
                    page_number=profile.page_number,
                    width=profile.width,
                    height=profile.height,
                    rotation=profile.rotation,  # type: ignore[arg-type]
                    elements=(),
                    tables=(),
                )
            )
            continue
        tables = {table.source_object_id: table for table in page.tables}
        failures = {
            source_id: reason
            for source_id, table in tables.items()
            if (reason := _table_failure(table)) is not None
        }
        elements: list[ExtractedElement] = []
        for element in page.elements:
            reason = failures.get(element.source_object_id)
            text = element.text or ""
            if element.element_type is ExtractedElementType.TABLE:
                table = tables.get(element.source_object_id)
                if table is None:
                    reason = "TABLE_STRUCTURE_MISSING"
                elif reason and not text.strip():
                    # Source sequence only: invalid row/column indices cannot define a grid.
                    text = "\n".join(cell.text for cell in table.cells)
            elif element.element_type is ExtractedElementType.UNKNOWN and text.strip():
                reason = "UNKNOWN_TEXT_STRUCTURE"
            if reason:
                element = _recover_text(element, reason, text)
                degraded += 1
                warnings.append(f"page {page.page_number}, {element.source_object_id}: {reason}")
            elements.append(element)
        pages.append(
            page.model_copy(
                update={
                    "elements": tuple(elements),
                    "tables": tuple(
                        table for table in page.tables if table.source_object_id not in failures
                    ),
                }
            )
        )
    prepared = result.model_copy(update={"pages": tuple(pages)})
    document = normalize_neutral_result(prepared, context)
    if not warnings:
        return RecoveryOutcome(document, ())
    missing_set = set(missing)
    document = document.model_copy(
        update={
            "pages": tuple(
                page.model_copy(update={"extensions": {RECOVERY_KEY: {"missing": True}}})
                if page.page_number in missing_set
                else page
                for page in document.pages
            ),
            "provenance": tuple(
                record.model_copy(
                    update={
                        "source_parser": None,
                        "parser_version": None,
                        "parser_run_id": None,
                        "source_coordinate_space": None,
                        "source_bbox": None,
                        "to_canonical_transform": None,
                        "operation": "MISSING_PAGE_PLACEHOLDER",
                    }
                )
                if record.page_number in missing_set
                else record
                for record in document.provenance
            ),
            "extensions": {
                RECOVERY_KEY: {
                    "version": RECOVERY_VERSION,
                    "missing_pages": missing,
                    "degraded_blocks": degraded,
                }
            },
            "processing": document.processing.model_copy(
                update={"normalizer_version": RECOVERY_VERSION}
            ),
        }
    )
    return RecoveryOutcome(DocumentIR.model_validate(document.model_dump()), tuple(warnings))

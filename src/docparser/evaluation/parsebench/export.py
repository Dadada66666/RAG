"""Canonical IR to the pinned official ParseBench ParseOutput representation."""

from __future__ import annotations

import html

from docparser.evaluation.parsebench.models import (
    PARSEBENCH_ADAPTER_VERSION,
    ParseBenchInferenceRequest,
    ParseBenchInferenceResult,
    ParseBenchLayoutItemIR,
    ParseBenchLayoutPageIR,
    ParseBenchLayoutSegmentIR,
    ParseBenchPageIR,
    ParseBenchParseOutput,
)
from docparser.ir.enums import BlockType, ReadingOrderStatus
from docparser.ir.models import Block, DocumentIR
from docparser.ir.tables import Table


def _table_html(table: Table) -> str:
    anchors = {(cell.row_index, cell.column_index): cell for cell in table.cells}
    covered: set[tuple[int, int]] = set()
    rows: list[str] = ["<table>"]
    for row in range(table.logical_row_count):
        rows.append("<tr>")
        for column in range(table.logical_column_count):
            if (row, column) in covered:
                continue
            cell = anchors.get((row, column))
            if cell is None:
                rows.append("<td></td>")
                continue
            tag = "th" if cell.is_header else "td"
            attributes = ""
            if cell.row_span > 1:
                attributes += f' rowspan="{cell.row_span}"'
            if cell.column_span > 1:
                attributes += f' colspan="{cell.column_span}"'
            rows.append(f"<{tag}{attributes}>{html.escape(cell.text)}</{tag}>")
            for occupied_row in range(row, row + cell.row_span):
                for occupied_column in range(column, column + cell.column_span):
                    if (occupied_row, occupied_column) != (row, column):
                        covered.add((occupied_row, occupied_column))
        rows.append("</tr>")
    rows.append("</table>")
    return "".join(rows)


def _block_markdown(block: Block, table_by_id: dict[str, Table]) -> str:
    if block.block_type is BlockType.TABLE and block.content_ref is not None:
        table = table_by_id.get(str(block.content_ref))
        return _table_html(table) if table is not None else (block.text or "")
    text = block.text or ""
    if block.block_type is BlockType.TITLE:
        return f"# {text}" if text else ""
    if block.block_type is BlockType.HEADING:
        return f"## {text}" if text else ""
    if block.block_type is BlockType.LIST_ITEM:
        return f"- {text}" if text else ""
    return text


def _layout_item(block: Block, markdown: str) -> ParseBenchLayoutItemIR:
    return ParseBenchLayoutItemIR(
        type=block.block_type.value.lower(),
        md=markdown,
        html=markdown if block.block_type is BlockType.TABLE else "",
        value=block.text or "",
        bbox=ParseBenchLayoutSegmentIR(
            x=block.bbox.x0,
            y=block.bbox.y0,
            w=block.bbox.width,
            h=block.bbox.height,
            confidence=block.confidence,
            label=block.block_type.value,
        ),
    )


def export_document_to_parsebench(
    document: DocumentIR,
    *,
    example_id: str,
    pipeline_name: str,
    source_file_path: str,
    latency_in_ms: int,
) -> ParseBenchInferenceResult:
    """Export a deterministic official-compatible prediction; do not evaluate it locally."""

    table_by_id = {str(table.table_id): table for table in document.tables}
    pages: list[ParseBenchPageIR] = []
    layout_pages: list[ParseBenchLayoutPageIR] = []
    document_markdown: list[str] = []
    for page in document.pages:
        ordered = sorted(
            page.blocks,
            key=lambda block: (
                block.reading_order
                if block.reading_order_status is ReadingOrderStatus.IN_FLOW
                and block.reading_order is not None
                else 10**9,
                str(block.block_id),
            ),
        )
        rendered = [
            (block, markdown)
            for block in ordered
            if (markdown := _block_markdown(block, table_by_id))
        ]
        page_markdown = "\n\n".join(markdown for _, markdown in rendered)
        pages.append(ParseBenchPageIR(page_index=page.page_number - 1, markdown=page_markdown))
        layout_pages.append(
            ParseBenchLayoutPageIR(
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                md=page_markdown,
                text="\n".join(block.text or "" for block, _ in rendered),
                original_orientation_angle=int(page.rotation_applied),
                items=tuple(_layout_item(block, markdown) for block, markdown in rendered),
            )
        )
        document_markdown.append(page_markdown)
    parser_run = document.processing.parser_runs[0] if document.processing.parser_runs else None
    started_at = str(parser_run.started_at) if parser_run is not None else str(document.created_at)
    completed_at = str(parser_run.ended_at) if parser_run is not None else str(document.created_at)
    output = ParseBenchParseOutput(
        example_id=example_id,
        pipeline_name=pipeline_name,
        pages=tuple(pages),
        layout_pages=tuple(layout_pages),
        markdown="\n\n".join(document_markdown),
    )
    return ParseBenchInferenceResult(
        request=ParseBenchInferenceRequest(
            example_id=example_id,
            source_file_path=source_file_path,
        ),
        pipeline_name=pipeline_name,
        raw_output={
            "adapter_version": PARSEBENCH_ADAPTER_VERSION,
            "document_id": str(document.document_id),
            "ir_revision_id": str(document.revision_id),
            "schema_version": document.schema_version,
        },
        output=output,
        started_at=started_at,
        completed_at=completed_at,
        latency_in_ms=latency_in_ms,
    )

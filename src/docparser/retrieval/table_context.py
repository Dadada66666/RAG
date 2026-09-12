"""Derived row maps over the exact Fixed source text; never rewrite embedding inputs."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from docparser.ir.base import StrictIRModel
from docparser.ir.enums import TableCellHeaderRole
from docparser.ir.tables import Table, TableSegment
from docparser.retrieval.chunking import Tokenizer, _render_cell

TABLE_MAP_VERSION = "table-source-map@1.0.0"


@dataclass(frozen=True, slots=True)
class SourceEncoding:
    token_ids: tuple[int, ...]
    offsets: tuple[tuple[int, int], ...]


@runtime_checkable
class OffsetTokenizer(Protocol):
    def encode_with_offsets(self, text: str) -> SourceEncoding | None: ...


class TableRowMap(StrictIRModel):
    row_index: int
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    band_start: int
    band_end: int
    cell_ids: tuple[str, ...]
    segment_ids: tuple[str, ...]


class TableSourceMap(StrictIRModel):
    version: str = TABLE_MAP_VERSION
    tokenizer_id: str
    alignment: Literal[
        "ALIGNED", "OFFSETS_UNAVAILABLE", "TOKEN_MISMATCH", "INVALID_OFFSETS", "RENDER_MISMATCH"
    ]
    rows: tuple[TableRowMap, ...]
    segments: tuple[TableSegment, ...]
    linked_source_ids: tuple[str, ...]
    column_header_rows: tuple[int, ...]
    unknown_header_roles: bool


def _bands(table: Table) -> list[tuple[int, int]]:
    """Merge only overlapping rowspan intervals; touching bands remain independent."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(
        (cell.row_index, cell.row_index + cell.row_span)
        for cell in table.cells
        if cell.row_span > 1
    ):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    bands = [(row, row + 1) for row in range(table.logical_row_count)]
    for start, end in merged:
        for row in range(start, end):
            bands[row] = (start, end)
    return bands


def _linked_sources(table: Table) -> dict[str, tuple[str, ...]]:
    segments = {segment.segment_id: segment for segment in table.segments}
    graph: dict[str, set[str]] = {str(segment.block_id): set() for segment in table.segments}
    for segment in table.segments:
        for linked in (segment.continued_from_segment_id, segment.continues_to_segment_id):
            if linked is not None:
                graph[str(segment.block_id)].add(str(segments[linked].block_id))
    result: dict[str, tuple[str, ...]] = {}
    for source in graph:
        if source in result:
            continue
        visited: set[str] = set()
        pending = [source]
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(graph[current] - visited)
        component = tuple(sorted(visited))
        result.update((identifier, component) for identifier in component)
    return result


def build_table_source_maps(
    table: Table,
    source_texts: dict[str, tuple[str, bool]],
    tokenizer: Tokenizer,
) -> dict[str, TableSourceMap]:
    """Index anchors once, then align each whole source with the original tokenizer settings."""
    anchors: list[dict[int, str]] = [{} for _ in range(table.logical_row_count)]
    cell_ids: list[list[str]] = [[] for _ in range(table.logical_row_count)]
    for cell in sorted(table.cells, key=lambda value: (value.row_index, value.column_index)):
        anchors[cell.row_index][cell.column_index] = _render_cell(cell)
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            cell_ids[row].append(str(cell.cell_id))
    row_texts = [
        "| "
        + " | ".join(row.get(column, "") for column in range(table.logical_column_count))
        + " |"
        for row in anchors
    ]
    bands = _bands(table)
    links = _linked_sources(table)
    result: dict[str, TableSourceMap] = {}
    for source_id, (text, ordered) in source_texts.items():
        segments = tuple(
            segment for segment in table.segments if str(segment.block_id) == source_id
        )
        segments = segments or table.segments  # Same explicit fallback as the Fixed renderer.
        row_segments: dict[int, list[str]] = {}
        for segment in segments:
            for row in range(segment.row_start, segment.row_end_exclusive):
                row_segments.setdefault(row, []).append(str(segment.segment_id))
        row_indices = tuple(row_segments)
        rendered = "\n".join(row_texts[row] for row in row_indices)
        alignment: Literal[
            "ALIGNED", "OFFSETS_UNAVAILABLE", "TOKEN_MISMATCH", "INVALID_OFFSETS", "RENDER_MISMATCH"
        ] = "OFFSETS_UNAVAILABLE"
        encoding = None
        full_text = text + ("\n\n" if ordered else "")
        if rendered != text:
            alignment = "RENDER_MISMATCH"
        elif isinstance(tokenizer, OffsetTokenizer):
            encoding = tokenizer.encode_with_offsets(full_text)
            if encoding is not None:
                alignment = "ALIGNED"
                if encoding.token_ids != tokenizer.encode(full_text):
                    alignment = "TOKEN_MISMATCH"
                elif len(encoding.offsets) != len(encoding.token_ids) or any(
                    not (0 <= start < end <= len(full_text))
                    or (
                        index > 0
                        and (
                            start < encoding.offsets[index - 1][0]
                            or end < encoding.offsets[index - 1][1]
                        )
                    )
                    for index, (start, end) in enumerate(encoding.offsets)
                ):
                    alignment = "INVALID_OFFSETS"
        rows: list[TableRowMap] = []
        if alignment == "ALIGNED":
            assert encoding is not None
            starts = [start for start, _ in encoding.offsets]
            ends = [end for _, end in encoding.offsets]
            char_start = 0
            for row in row_indices:
                char_end = char_start + len(row_texts[row])
                left, right = bisect_right(ends, char_start), bisect_left(starts, char_end)
                if left >= right:
                    alignment = "INVALID_OFFSETS"
                    rows = []
                    break
                rows.append(
                    TableRowMap(
                        row_index=row,
                        char_start=char_start,
                        char_end=char_end,
                        token_start=left,
                        token_end=right,
                        band_start=bands[row][0],
                        band_end=bands[row][1],
                        cell_ids=tuple(cell_ids[row]),
                        segment_ids=tuple(row_segments[row]),
                    )
                )
                char_start = char_end + 1
        result[source_id] = TableSourceMap(
            tokenizer_id=tokenizer.tokenizer_id,
            alignment=alignment,
            rows=tuple(rows),
            segments=segments,
            linked_source_ids=links.get(source_id, (source_id,)),
            column_header_rows=table.header_row_indices,
            unknown_header_roles=any(
                cell.header_role is TableCellHeaderRole.UNKNOWN for cell in table.cells
            ),
        )
    return result

"""Map the pinned MinerU 3.4.5 Hybrid High middle.json contract."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, cast

from docparser.domain.parser_contract import (
    CoordinateOrigin,
    CoordinateUnit,
    ExtractedElement,
    ExtractedElementType,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTextSpan,
    PageParseResult,
    ParserDescriptor,
    ParseResult,
    ParserRun,
    ParseStatus,
    SourceBBox,
)
from docparser.ir.enums import TableCellHeaderRole

JsonObject = dict[str, Any]

_TABLE_IDENTITY = re.compile(r"\bTable\s+([0-9]+(?:[-.]\w+)*)", re.IGNORECASE)
_SIMPLE_TYPES: dict[str, ExtractedElementType] = {
    "text": ExtractedElementType.PARAGRAPH,
    "aside_text": ExtractedElementType.PARAGRAPH,
    "page_footnote": ExtractedElementType.FOOTNOTE,
    "interline_equation": ExtractedElementType.EQUATION,
}
_DECORATIVE_TYPES: dict[str, ExtractedElementType] = {
    "header": ExtractedElementType.HEADER,
    "footer": ExtractedElementType.FOOTER,
    "page_number": ExtractedElementType.PAGE_NUMBER,
}
_COMPOSITE_CAPTIONS = {
    "table_caption",
    "image_caption",
    "chart_caption",
}
_COMPOSITE_FOOTNOTES = {
    "table_footnote",
    "image_footnote",
    "chart_footnote",
}


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"MinerU {label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"MinerU {label} must be an array")
    return value


def _bbox(value: object, label: str) -> SourceBBox:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"MinerU {label} bbox must contain four coordinates")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"MinerU {label} bbox must be numeric")
    return SourceBBox(
        x0=float(value[0]),
        y0=float(value[1]),
        x1=float(value[2]),
        y1=float(value[3]),
        origin=CoordinateOrigin.TOP_LEFT,
    )


def _score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("MinerU span score must be numeric")
    return float(value)


def _metadata(block: JsonObject, *, source_layer: str) -> JsonObject:
    block_type = block.get("type")
    index = block.get("index")
    if not isinstance(block_type, str):
        raise ValueError("MinerU block type must be a string")
    if index is not None and (isinstance(index, bool) or not isinstance(index, (int, float))):
        raise ValueError("MinerU block index must be numeric")
    result: JsonObject = {
        "org.mineru.block_type": block_type,
        "org.mineru.source_layer": source_layer,
    }
    if index is not None:
        result["org.mineru.index"] = index
    level = block.get("level")
    if level is not None:
        if isinstance(level, bool) or not isinstance(level, int) or level < 1:
            raise ValueError("MinerU title level must be an integer >= 1")
        result["org.mineru.heading_level"] = level
    return result


def _text_and_spans(
    block: JsonObject,
    *,
    source_prefix: str,
) -> tuple[str | None, tuple[ExtractedTextSpan, ...]]:
    raw_lines = _array(block.get("lines"), "block.lines")
    parts: list[str] = []
    spans: list[ExtractedTextSpan] = []
    offset = 0
    for line_index, raw_line in enumerate(raw_lines):
        line = _object(raw_line, "line")
        raw_spans = _array(line.get("spans"), "line.spans")
        if line_index > 0:
            parts.append("\n")
            offset += 1
        for span_index, raw_span in enumerate(raw_spans):
            span = _object(raw_span, "span")
            span_type = span.get("type")
            if span_type not in {"text", "inline_equation", "interline_equation"}:
                raise ValueError(f"unsupported MinerU textual span type: {span_type!r}")
            content = span.get("content")
            if not isinstance(content, str):
                raise ValueError("MinerU textual span content must be a string")
            if not content:
                continue
            start = offset
            parts.append(content)
            offset += len(content)
            spans.append(
                ExtractedTextSpan(
                    source_object_id=f"{source_prefix}/span/{line_index}/{span_index}",
                    start=start,
                    end=offset,
                    bbox=_bbox(span.get("bbox"), "span"),
                    confidence=_score(span.get("score")),
                )
            )
    text = "".join(parts)
    return (text or None), tuple(spans)


def _body_span(block: JsonObject, *, expected_type: str) -> JsonObject:
    matches: list[JsonObject] = []
    for raw_line in _array(block.get("lines"), "body.lines"):
        line = _object(raw_line, "body line")
        for raw_span in _array(line.get("spans"), "body line.spans"):
            span = _object(raw_span, "body span")
            if span.get("type") == expected_type:
                matches.append(span)
    if len(matches) != 1:
        raise ValueError(f"MinerU {expected_type} body must contain exactly one matching span")
    return matches[0]


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int, bool]]] = []
        self._row: list[tuple[str, int, int, bool]] | None = None
        self._cell: list[str] | None = None
        self._rowspan = 1
        self._colspan = 1
        self._header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            values = dict(attrs)
            self._rowspan = int(values.get("rowspan") or 1)
            self._colspan = int(values.get("colspan") or 1)
            if self._rowspan < 1 or self._colspan < 1:
                raise ValueError("MinerU table spans must be positive")
            self._header = tag == "th"
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(
                ("".join(self._cell).strip(), self._rowspan, self._colspan, self._header)
            )
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def table_cells_from_html(
    html: str,
    *,
    table_id: str,
) -> tuple[int, int, tuple[ExtractedTableCell, ...], tuple[tuple[str, ...], ...]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    occupied: set[tuple[int, int]] = set()
    cells: list[ExtractedTableCell] = []
    max_column = 0
    signatures: list[tuple[str, ...]] = []
    for row_index, row in enumerate(parser.rows):
        column_index = 0
        signatures.append(tuple(_normalize_cell_text(cell[0]) for cell in row))
        for text, row_span, column_span, explicit_header in row:
            while (row_index, column_index) in occupied:
                column_index += 1
            for row_value in range(row_index, row_index + row_span):
                for column_value in range(column_index, column_index + column_span):
                    occupied.add((row_value, column_value))
            cells.append(
                ExtractedTableCell(
                    source_object_id=f"{table_id}/cell/{len(cells)}",
                    row_index=row_index,
                    column_index=column_index,
                    row_span=row_span,
                    column_span=column_span,
                    text=text,
                    is_header=explicit_header,
                    header_role=(
                        TableCellHeaderRole.UNKNOWN if explicit_header else TableCellHeaderRole.NONE
                    ),
                    bbox=None,
                    confidence=None,
                )
            )
            column_index += column_span
            max_column = max(max_column, column_index)
    if not cells:
        raise ValueError("MinerU table HTML contained no logical cells")
    row_count = max(row + 1 for row, _ in occupied)
    column_count = max(max_column, max(column + 1 for _, column in occupied))
    return row_count, column_count, tuple(cells), tuple(signatures)


def _normalize_cell_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFC", value).split())


def _table_identities(texts: list[str]) -> set[str]:
    return {match.group(1).casefold() for text in texts for match in _TABLE_IDENTITY.finditer(text)}


def _relative_asset_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return None
    path = PurePosixPath(value.replace("\\", "/"))
    if ".." in path.parts:
        return None
    return str(path)


def _bounded_visual_analysis(content: str) -> object:
    encoded = content.encode("utf-8")
    if len(encoded) <= 8 * 1024:
        return content
    return {
        "content_omitted": True,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    }


@dataclass(frozen=True, slots=True)
class _Fragment:
    source_object_id: str
    page_number: int
    rows: tuple[tuple[str, ...], ...]
    table: ExtractedTable


@dataclass
class _PageElements:
    page_number: int
    elements: list[ExtractedElement]
    next_order: int = 0

    def append(
        self,
        source_id: str,
        kind: ExtractedElementType,
        raw: JsonObject,
        *,
        text: str | None,
        spans: tuple[ExtractedTextSpan, ...],
        metadata: JsonObject,
        parent: str | None = None,
        caption_for: str | None = None,
        decorative: bool = False,
    ) -> None:
        order = None if decorative else self.next_order
        if not decorative:
            self.next_order += 1
        self.elements.append(
            _element(
                source_object_id=source_id,
                kind=kind,
                page_number=self.page_number,
                bbox=_bbox(raw.get("bbox"), "block"),
                text=text,
                text_spans=spans,
                reading_order=order,
                decorative=decorative,
                method=_method(kind),
                metadata=metadata,
                parent=parent,
                caption_for=caption_for,
            )
        )


def _element(
    *,
    source_object_id: str,
    kind: ExtractedElementType,
    page_number: int,
    bbox: SourceBBox,
    text: str | None,
    text_spans: tuple[ExtractedTextSpan, ...],
    reading_order: int | None,
    decorative: bool,
    method: Literal[
        "PDF_TEXT", "OCR", "VLM", "LAYOUT_MODEL", "TABLE_MODEL", "FORMULA_MODEL", "IMPORTED"
    ],
    metadata: JsonObject,
    parent: str | None = None,
    caption_for: str | None = None,
) -> ExtractedElement:
    return ExtractedElement(
        source_object_id=source_object_id,
        element_type=kind,
        page_number=page_number,
        bbox=bbox,
        text=text,
        reading_order=reading_order,
        reading_order_resolved=reading_order is not None and not decorative,
        decorative=decorative,
        language=None,
        confidence=None,
        extraction_method=method,
        parent_source_object_id=parent,
        caption_for_source_object_id=caption_for,
        text_spans=text_spans,
        metadata=metadata,
    )


def _method(
    kind: ExtractedElementType,
) -> Literal["PDF_TEXT", "OCR", "VLM", "LAYOUT_MODEL", "TABLE_MODEL", "FORMULA_MODEL", "IMPORTED"]:
    if kind is ExtractedElementType.TABLE:
        return "TABLE_MODEL"
    if kind is ExtractedElementType.FIGURE:
        return "LAYOUT_MODEL"
    if kind is ExtractedElementType.EQUATION:
        return "FORMULA_MODEL"
    return "IMPORTED"


def _title_kind(block: JsonObject) -> ExtractedElementType:
    level = block.get("level")
    return ExtractedElementType.TITLE if level == 1 else ExtractedElementType.HEADING


def _match_fragment(
    rows: tuple[tuple[str, ...], ...],
    merged_rows: tuple[tuple[str, ...], ...],
    offset: int,
) -> int | None:
    for repeated in range(len(rows)):
        if repeated and rows[:repeated] != merged_rows[:repeated]:
            continue
        remaining = rows[repeated:]
        if remaining and merged_rows[offset : offset + len(remaining)] == remaining:
            return offset + len(remaining)
    return None


def _apply_continuations(
    pages: list[JsonObject],
    fragments: list[_Fragment],
    warnings: list[str],
) -> dict[str, ExtractedTable]:
    by_page: dict[int, list[_Fragment]] = defaultdict(list)
    for fragment in fragments:
        by_page[fragment.page_number].append(fragment)
    linked: set[str] = set()
    replacements = {fragment.source_object_id: fragment.table for fragment in fragments}
    for page in pages:
        page_number = cast(int, page["page_idx"]) + 1
        for raw_para in _array(page.get("para_blocks"), "page.para_blocks"):
            para = _object(raw_para, "para block")
            if para.get("type") != "table":
                continue
            body_blocks = [
                _object(raw, "para table child")
                for raw in _array(para.get("blocks"), "para table.blocks")
                if _object(raw, "para table child").get("type") == "table_body"
            ]
            if len(body_blocks) != 1:
                raise ValueError("MinerU para table must contain one table_body")
            if body_blocks[0].get("lines_deleted") is True:
                continue
            body_span = _body_span(body_blocks[0], expected_type="table")
            html = body_span.get("html")
            if not isinstance(html, str):
                raise ValueError("MinerU para table body omitted html")
            _, _, _, merged_rows = table_cells_from_html(html, table_id="mineru:merged")
            starts = [
                fragment
                for fragment in by_page[page_number]
                if fragment.source_object_id not in linked
                and len(merged_rows) > len(fragment.rows)
                and merged_rows[: len(fragment.rows)] == fragment.rows
            ]
            if not starts:
                if any(len(merged_rows) > len(fragment.rows) for fragment in by_page[page_number]):
                    warnings.append(
                        f"page {page_number}: MinerU merged table has no exact local start"
                    )
                continue
            if len(starts) != 1:
                warnings.append(
                    "page "
                    f"{page_number}: MinerU merged table did not identify one exact local start"
                )
                continue
            chain = [starts[0]]
            offset = len(starts[0].rows)
            current_page = page_number
            while offset < len(merged_rows):
                current_page += 1
                candidates: list[tuple[_Fragment, int]] = []
                for fragment in by_page.get(current_page, []):
                    if fragment.source_object_id in linked:
                        continue
                    if fragment.table.column_count != chain[0].table.column_count:
                        continue
                    next_offset = _match_fragment(fragment.rows, merged_rows, offset)
                    if next_offset is not None:
                        candidates.append((fragment, next_offset))
                if len(candidates) != 1:
                    break
                fragment, offset = candidates[0]
                chain.append(fragment)
            if offset != len(merged_rows) or len(chain) < 2:
                warnings.append(
                    f"page {page_number}: MinerU merged table failed exact page-fragment alignment"
                )
                continue
            for index, fragment in enumerate(chain):
                previous = chain[index - 1].source_object_id if index else None
                following = chain[index + 1].source_object_id if index + 1 < len(chain) else None
                replacements[fragment.source_object_id] = fragment.table.model_copy(
                    update={
                        "continuation_from_source_object_id": previous,
                        "continuation_to_source_object_id": following,
                    }
                )
                linked.add(fragment.source_object_id)
    return replacements


def map_mineru_middle(
    payload: JsonObject,
    *,
    descriptor: ParserDescriptor,
    run: ParserRun,
) -> ParseResult:
    if payload.get("_backend") != "hybrid":
        raise ValueError("MinerU middle.json backend must be hybrid")
    if payload.get("_effort") != "high":
        raise ValueError("MinerU middle.json effort must be high")
    if payload.get("_version_name") != "3.4.5":
        raise ValueError("MinerU middle.json version must be 3.4.5")
    raw_pages = [_object(raw, "page") for raw in _array(payload.get("pdf_info"), "pdf_info")]
    if [page.get("page_idx") for page in raw_pages] != list(range(len(raw_pages))):
        raise ValueError("MinerU pdf_info pages must be contiguous and zero-based")

    warnings: list[str] = []
    page_elements: dict[int, list[ExtractedElement]] = {}
    page_tables: dict[int, list[ExtractedTable]] = {}
    fragments: list[_Fragment] = []

    for page in raw_pages:
        page_index = cast(int, page["page_idx"])
        page_number = page_index + 1
        size = page.get("page_size")
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in size)
        ):
            raise ValueError("MinerU page_size must be [width, height]")
        elements: list[ExtractedElement] = []
        tables: list[ExtractedTable] = []
        append = _PageElements(page_number=page_number, elements=elements).append

        for block_position, raw_block in enumerate(
            _array(page.get("preproc_blocks"), "page.preproc_blocks")
        ):
            block = _object(raw_block, "preproc block")
            block_type = block.get("type")
            if not isinstance(block_type, str):
                raise ValueError("MinerU preproc block type must be a string")
            source_id = f"mineru:{page_number}:preproc:{block_position}"
            metadata = _metadata(block, source_layer="preproc_blocks")
            if block_type == "title":
                text, spans = _text_and_spans(block, source_prefix=source_id)
                append(
                    source_id,
                    _title_kind(block),
                    block,
                    text=text,
                    spans=spans,
                    metadata=metadata,
                )
            elif block_type in _SIMPLE_TYPES:
                text, spans = _text_and_spans(block, source_prefix=source_id)
                append(
                    source_id,
                    _SIMPLE_TYPES[block_type],
                    block,
                    text=text,
                    spans=spans,
                    metadata=metadata,
                )
            elif block_type == "list":
                append(
                    source_id,
                    ExtractedElementType.LIST,
                    block,
                    text=None,
                    spans=(),
                    metadata=metadata,
                )
                for child_index, raw_child in enumerate(_array(block.get("blocks"), "list.blocks")):
                    child = _object(raw_child, "list item")
                    child_id = f"{source_id}/item/{child_index}"
                    text, spans = _text_and_spans(child, source_prefix=child_id)
                    append(
                        child_id,
                        ExtractedElementType.LIST_ITEM,
                        child,
                        text=text,
                        spans=spans,
                        metadata=_metadata(child, source_layer="preproc_blocks"),
                        parent=source_id,
                    )
            elif block_type == "code":
                children = [
                    _object(raw, "code child") for raw in _array(block.get("blocks"), "code.blocks")
                ]
                bodies = [child for child in children if child.get("type") == "code_body"]
                if len(bodies) != 1:
                    raise ValueError("MinerU code block must contain one code_body")
                text, spans = _text_and_spans(bodies[0], source_prefix=source_id)
                append(
                    source_id,
                    ExtractedElementType.CODE,
                    block,
                    text=text,
                    spans=spans,
                    metadata=metadata,
                )
            elif block_type in {"table", "image", "chart"}:
                children = [
                    _object(raw, f"{block_type} child")
                    for raw in _array(block.get("blocks"), f"{block_type}.blocks")
                ]
                body_type = f"{block_type}_body"
                bodies = [child for child in children if child.get("type") == body_type]
                if len(bodies) != 1:
                    raise ValueError(f"MinerU {block_type} block must contain one {body_type}")
                caption_children = [
                    (index, child)
                    for index, child in enumerate(children)
                    if child.get("type") in _COMPOSITE_CAPTIONS
                ]
                caption_texts: list[str] = []
                caption_payloads: list[
                    tuple[int, JsonObject, str, str | None, tuple[ExtractedTextSpan, ...]]
                ] = []
                for child_index, child in caption_children:
                    child_id = f"{source_id}/caption/{child_index}"
                    text, spans = _text_and_spans(child, source_prefix=child_id)
                    caption_texts.append(text or "")
                    caption_payloads.append((child_index, child, child_id, text, spans))
                identities = _table_identities(caption_texts) if block_type == "table" else set()
                ambiguous = block_type == "table" and len(identities) > 1
                safe_caption_ids = (
                    {caption_payloads[0][2]}
                    if block_type == "table" and len(caption_payloads) == 1 and not ambiguous
                    else (
                        {item[2] for item in caption_payloads} if block_type != "table" else set()
                    )
                )
                if ambiguous:
                    metadata["org.docparser.structural_ambiguity"] = "MULTIPLE_TABLE_IDENTITIES"
                body = bodies[0]
                body_position = children.index(body)
                for child_index, child in enumerate(children):
                    if child_index == body_position:
                        if block_type == "table":
                            body_span = _body_span(body, expected_type="table")
                            html = body_span.get("html")
                            if not isinstance(html, str):
                                raise ValueError("MinerU table body omitted html")
                            row_count, column_count, cells, rows = table_cells_from_html(
                                html,
                                table_id=source_id,
                            )
                            append(
                                source_id,
                                ExtractedElementType.TABLE,
                                block,
                                text=None,
                                spans=(),
                                metadata=metadata,
                            )
                            table = ExtractedTable(
                                source_object_id=source_id,
                                page_number=page_number,
                                bbox=_bbox(block.get("bbox"), "table"),
                                row_count=row_count,
                                column_count=column_count,
                                cells=cells,
                                caption_source_object_ids=tuple(
                                    item[2]
                                    for item in caption_payloads
                                    if item[2] in safe_caption_ids
                                ),
                                continuation_from_source_object_id=None,
                                continuation_to_source_object_id=None,
                                confidence=None,
                                metadata=metadata,
                            )
                            tables.append(table)
                            fragments.append(
                                _Fragment(
                                    source_object_id=source_id,
                                    page_number=page_number,
                                    rows=rows,
                                    table=table,
                                )
                            )
                        else:
                            visual_span = _body_span(body, expected_type=block_type)
                            analysis = visual_span.get("content")
                            visual_metadata = dict(metadata)
                            if isinstance(analysis, str) and analysis:
                                visual_metadata["org.mineru.visual_analysis_present"] = True
                                visual_metadata["org.mineru.generated_visual_analysis"] = (
                                    _bounded_visual_analysis(analysis)
                                )
                            else:
                                visual_metadata["org.mineru.visual_analysis_present"] = False
                            asset_path = _relative_asset_path(visual_span.get("image_path"))
                            if asset_path is not None:
                                visual_metadata["org.mineru.image_path"] = asset_path
                            append(
                                source_id,
                                ExtractedElementType.FIGURE,
                                block,
                                text=None,
                                spans=(),
                                metadata=visual_metadata,
                            )
                        continue
                    child_type = child.get("type")
                    if child_type in _COMPOSITE_CAPTIONS:
                        payload_item = next(
                            item for item in caption_payloads if item[0] == child_index
                        )
                        _, _, child_id, text, spans = payload_item
                        append(
                            child_id,
                            ExtractedElementType.FIGURE_CAPTION,
                            child,
                            text=text,
                            spans=spans,
                            metadata=_metadata(child, source_layer="preproc_blocks"),
                            parent=source_id,
                            caption_for=(source_id if child_id in safe_caption_ids else None),
                        )
                    elif child_type in _COMPOSITE_FOOTNOTES:
                        child_id = f"{source_id}/footnote/{child_index}"
                        text, spans = _text_and_spans(child, source_prefix=child_id)
                        append(
                            child_id,
                            ExtractedElementType.FOOTNOTE,
                            child,
                            text=text,
                            spans=spans,
                            metadata=_metadata(child, source_layer="preproc_blocks"),
                            parent=source_id,
                        )
            else:
                text, spans = _text_and_spans(block, source_prefix=source_id)
                warnings.append(f"page {page_number}: unmapped MinerU block type: {block_type}")
                append(
                    source_id,
                    ExtractedElementType.UNKNOWN,
                    block,
                    text=text,
                    spans=spans,
                    metadata=metadata,
                )

        for discarded_position, raw_block in enumerate(
            _array(page.get("discarded_blocks"), "page.discarded_blocks")
        ):
            block = _object(raw_block, "discarded block")
            block_type = block.get("type")
            if block_type not in _DECORATIVE_TYPES:
                raise ValueError(f"unsupported MinerU discarded block type: {block_type}")
            source_id = f"mineru:{page_number}:discarded:{discarded_position}"
            text, spans = _text_and_spans(block, source_prefix=source_id)
            append(
                source_id,
                _DECORATIVE_TYPES[cast(str, block_type)],
                block,
                text=text,
                spans=spans,
                metadata=_metadata(block, source_layer="discarded_blocks"),
                decorative=True,
            )
        page_elements[page_number] = elements
        page_tables[page_number] = tables

    replacements = _apply_continuations(raw_pages, fragments, warnings)
    pages: list[PageParseResult] = []
    for raw_page in raw_pages:
        page_index = cast(int, raw_page["page_idx"])
        page_number = page_index + 1
        size = cast(list[object], raw_page["page_size"])
        pages.append(
            PageParseResult(
                page_number=page_number,
                width=float(cast(int | float, size[0])),
                height=float(cast(int | float, size[1])),
                rotation=0,
                coordinate_unit=CoordinateUnit.POINT,
                elements=tuple(page_elements[page_number]),
                tables=tuple(
                    replacements[table.source_object_id] for table in page_tables[page_number]
                ),
                warnings=(),
            )
        )
    requested = tuple(page.page_number for page in pages)
    return ParseResult(
        status=ParseStatus.SUCCESS,
        descriptor=descriptor,
        run=run,
        pages_requested=requested,
        pages=tuple(pages),
        warnings=tuple(warnings),
        errors=(),
    )

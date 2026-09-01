"""Map PaddleOCR-VL-1.6 structured page output to the neutral contract."""

from __future__ import annotations

from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any, Literal, cast

from docparser.domain.parser_contract import (
    CoordinateOrigin,
    CoordinateUnit,
    ExtractedElement,
    ExtractedElementType,
    ExtractedTable,
    ExtractedTableCell,
    PageParseResult,
    ParserDescriptor,
    ParseResult,
    ParserRun,
    ParseStatus,
    SourceBBox,
)

JsonObject = dict[str, Any]

_LABELS: dict[str, ExtractedElementType] = {
    "doc_title": ExtractedElementType.TITLE,
    "paragraph_title": ExtractedElementType.HEADING,
    "text": ExtractedElementType.PARAGRAPH,
    "paragraph": ExtractedElementType.PARAGRAPH,
    "list": ExtractedElementType.LIST,
    "table": ExtractedElementType.TABLE,
    "image": ExtractedElementType.FIGURE,
    "figure": ExtractedElementType.FIGURE,
    "figure_caption": ExtractedElementType.FIGURE_CAPTION,
    "image_caption": ExtractedElementType.FIGURE_CAPTION,
    "formula": ExtractedElementType.EQUATION,
    "algorithm": ExtractedElementType.CODE,
    "footnote": ExtractedElementType.FOOTNOTE,
    "vision_footnote": ExtractedElementType.FOOTNOTE,
    "header": ExtractedElementType.HEADER,
    "footer": ExtractedElementType.FOOTER,
    "number": ExtractedElementType.PAGE_NUMBER,
}


def _object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _bbox(value: object) -> SourceBBox:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Paddle block_bbox must contain four coordinates")
    coordinates = [float(item) for item in value]
    return SourceBBox(
        x0=min(coordinates[0], coordinates[2]),
        y0=min(coordinates[1], coordinates[3]),
        x1=max(coordinates[0], coordinates[2]),
        y1=max(coordinates[1], coordinates[3]),
        origin=CoordinateOrigin.TOP_LEFT,
    )


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
            self._rowspan = max(1, int(values.get("rowspan") or 1))
            self._colspan = max(1, int(values.get("colspan") or 1))
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
    html: str, *, table_id: str
) -> tuple[int, int, tuple[ExtractedTableCell, ...]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    occupied: set[tuple[int, int]] = set()
    cells: list[ExtractedTableCell] = []
    max_column = 0
    for row_index, row in enumerate(parser.rows):
        column_index = 0
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
                    bbox=None,
                    confidence=None,
                )
            )
            column_index += column_span
            max_column = max(max_column, column_index)
    if not cells:
        raise ValueError("Paddle table HTML contained no cells")
    row_count = max(row + 1 for row, _ in occupied)
    column_count = max(max_column, max(column + 1 for _, column in occupied))
    return row_count, column_count, tuple(cells)


def _element(page_number: int, raw: JsonObject, index: int) -> ExtractedElement:
    label = str(raw.get("block_label", "unknown")).lower()
    kind = _LABELS.get(label, ExtractedElementType.UNKNOWN)
    source_id = str(raw.get("block_id", f"page-{page_number}-block-{index}"))
    decorative = kind in {
        ExtractedElementType.HEADER,
        ExtractedElementType.FOOTER,
        ExtractedElementType.PAGE_NUMBER,
    }
    order = raw.get("block_order")
    resolved = isinstance(order, int) and not decorative
    method: Literal[
        "PDF_TEXT", "OCR", "VLM", "LAYOUT_MODEL", "TABLE_MODEL", "FORMULA_MODEL", "IMPORTED"
    ] = "VLM"
    if kind is ExtractedElementType.FIGURE:
        method = "LAYOUT_MODEL"
    elif kind is ExtractedElementType.TABLE:
        method = "TABLE_MODEL"
    elif kind is ExtractedElementType.EQUATION:
        method = "FORMULA_MODEL"
    return ExtractedElement(
        source_object_id=f"paddle:{page_number}:{source_id}",
        element_type=kind,
        page_number=page_number,
        bbox=_bbox(raw.get("block_bbox")),
        text=str(raw.get("block_content", "")) or None,
        reading_order=order if isinstance(order, int) and resolved else None,
        reading_order_resolved=resolved,
        decorative=decorative,
        language=None,
        confidence=None,
        extraction_method=method,
        parent_source_object_id=(
            f"paddle:{page_number}:{raw['parent_block_id']}"
            if raw.get("parent_block_id") is not None else None
        ),
        caption_for_source_object_id=None,
        metadata={"org.paddleocr.label": label},
    )


def map_paddleocr_vl_pages(
    payloads: list[JsonObject], *, descriptor: ParserDescriptor, run: ParserRun
) -> ParseResult:
    pages: list[PageParseResult] = []
    warnings: list[str] = []
    for fallback_index, payload in enumerate(payloads):
        page_number = int(payload.get("page_index", fallback_index)) + 1
        width = float(payload["source_width"])
        height = float(payload["source_height"])
        raw_blocks = payload.get("parsing_res_list")
        if not isinstance(raw_blocks, list):
            raise ValueError("Paddle page has no parsing_res_list")
        raw_mappings = [_object(raw) for raw in raw_blocks if isinstance(raw, Mapping)]
        elements = tuple(
            _element(page_number, raw, index) for index, raw in enumerate(raw_mappings)
        )
        tables: list[ExtractedTable] = []
        for element, raw in zip(elements, raw_mappings, strict=True):
            if element.element_type is not ExtractedElementType.TABLE:
                continue
            try:
                row_count, column_count, cells = table_cells_from_html(
                    str(raw.get("block_content", "")), table_id=element.source_object_id
                )
            except ValueError:
                warnings.append(
                    f"page {page_number}: table {element.source_object_id} lacked logical cells"
                )
                continue
            tables.append(
                ExtractedTable(
                    source_object_id=element.source_object_id,
                    page_number=page_number,
                    bbox=element.bbox,
                    row_count=row_count,
                    column_count=column_count,
                    cells=cells,
                    continuation_from_source_object_id=(
                        f"paddle:{page_number}:{raw['continuation_from_block_id']}"
                        if raw.get("continuation_from_block_id") is not None
                        else None
                    ),
                    continuation_to_source_object_id=(
                        f"paddle:{page_number}:{raw['continuation_to_block_id']}"
                        if raw.get("continuation_to_block_id") is not None
                        else None
                    ),
                    confidence=None,
                )
            )
        rotation_value = int(payload.get("rotation", 0)) % 360
        if rotation_value not in {0, 90, 180, 270}:
            raise ValueError("Paddle page rotation is invalid")
        pages.append(
            PageParseResult(
                page_number=page_number,
                width=width,
                height=height,
                rotation=cast(Literal[0, 90, 180, 270], rotation_value),
                coordinate_unit=CoordinateUnit.PIXEL,
                elements=elements,
                tables=tuple(tables),
                warnings=(),
            )
        )
    pages.sort(key=lambda page: page.page_number)
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

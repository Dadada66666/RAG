"""Map sanitized DoclingDocument JSON into the parser-neutral envelope."""

from __future__ import annotations

from collections.abc import Mapping
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
    "title": ExtractedElementType.TITLE,
    "section_header": ExtractedElementType.HEADING,
    "paragraph": ExtractedElementType.PARAGRAPH,
    "text": ExtractedElementType.PARAGRAPH,
    "list": ExtractedElementType.LIST,
    "list_item": ExtractedElementType.LIST_ITEM,
    "table": ExtractedElementType.TABLE,
    "picture": ExtractedElementType.FIGURE,
    "caption": ExtractedElementType.FIGURE_CAPTION,
    "formula": ExtractedElementType.EQUATION,
    "code": ExtractedElementType.CODE,
    "footnote": ExtractedElementType.FOOTNOTE,
    "page_header": ExtractedElementType.HEADER,
    "page_footer": ExtractedElementType.FOOTER,
    "page_number": ExtractedElementType.PAGE_NUMBER,
}


def _object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _objects(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [_object(item) for item in value if isinstance(item, Mapping)]


def _ref(value: object) -> str | None:
    ref = _object(value).get("cref")
    return ref if isinstance(ref, str) else None


def _first_provenance(item: JsonObject) -> JsonObject | None:
    prov = _objects(item.get("prov"))
    return prov[0] if prov else None


def _source_bbox(value: object) -> SourceBBox:
    bbox = _object(value)
    origin_value = str(bbox.get("coord_origin", "TOPLEFT")).upper()
    origin = CoordinateOrigin.BOTTOM_LEFT if "BOTTOM" in origin_value else CoordinateOrigin.TOP_LEFT
    left = float(bbox["l"])
    right = float(bbox["r"])
    first_y = float(bbox["t"])
    second_y = float(bbox["b"])
    return SourceBBox(
        x0=min(left, right),
        y0=min(first_y, second_y),
        x1=max(left, right),
        y1=max(first_y, second_y),
        origin=origin,
    )


def _page_number(item: JsonObject) -> int | None:
    prov = _first_provenance(item)
    if prov is None:
        return None
    value = prov.get("page_no")
    return int(value) if isinstance(value, int) else None


def _item_bbox(item: JsonObject) -> SourceBBox | None:
    prov = _first_provenance(item)
    if prov is None or not isinstance(prov.get("bbox"), Mapping):
        return None
    try:
        return _source_bbox(prov["bbox"])
    except (KeyError, TypeError, ValueError):
        return None


def _expand_refs(root: JsonObject, indexed: dict[str, JsonObject]) -> list[str]:
    ordered: list[str] = []

    def visit(ref_value: object) -> None:
        cref = _ref(ref_value)
        if cref is None:
            return
        item = indexed.get(cref)
        if item is None:
            return
        children = item.get("children")
        if cref.startswith("#/groups/") and isinstance(children, list):
            for child in children:
                visit(child)
            return
        ordered.append(cref)

    for child in _objects(root.get("children")):
        visit(child)
    return ordered


def _caption_targets(payload: JsonObject) -> dict[str, str]:
    targets: dict[str, str] = {}
    for item in _objects(payload.get("tables")) + _objects(payload.get("pictures")):
        source_id = str(item.get("self_ref", ""))
        for caption in _objects(item.get("captions")):
            caption_ref = _ref(caption)
            if caption_ref is not None:
                targets[caption_ref] = source_id
    return targets


def _reading_order(body_refs: list[str], indexed: dict[str, JsonObject]) -> dict[str, int]:
    page_counters: dict[int, int] = {}
    resolved: dict[str, int] = {}
    for ref in body_refs:
        item = indexed.get(ref)
        if item is None:
            continue
        page_number = _page_number(item)
        if page_number is None:
            continue
        resolved[ref] = page_counters.get(page_number, 0)
        page_counters[page_number] = resolved[ref] + 1
    return resolved


def _element(
    item: JsonObject,
    *,
    order: dict[str, int],
    caption_targets: dict[str, str],
) -> ExtractedElement | None:
    source_id = item.get("self_ref")
    page_number = _page_number(item)
    bbox = _item_bbox(item)
    if not isinstance(source_id, str) or page_number is None or bbox is None:
        return None
    label = str(item.get("label", "unknown")).lower()
    kind = _LABELS.get(label, ExtractedElementType.UNKNOWN)
    if source_id in caption_targets:
        kind = ExtractedElementType.FIGURE_CAPTION
    decorative = str(item.get("content_layer", "")).lower() == "furniture" or kind in {
        ExtractedElementType.HEADER,
        ExtractedElementType.FOOTER,
        ExtractedElementType.PAGE_NUMBER,
    }
    resolved = source_id in order and not decorative
    method: Literal[
        "PDF_TEXT", "OCR", "LAYOUT_MODEL", "TABLE_MODEL", "FORMULA_MODEL", "IMPORTED"
    ] = "IMPORTED"
    if kind is ExtractedElementType.TABLE:
        method = "TABLE_MODEL"
    elif kind is ExtractedElementType.FIGURE:
        method = "LAYOUT_MODEL"
    elif kind is ExtractedElementType.EQUATION:
        method = "FORMULA_MODEL"
    confidence = item.get("confidence")
    return ExtractedElement(
        source_object_id=source_id,
        element_type=kind,
        page_number=page_number,
        bbox=bbox,
        text=item.get("text") if isinstance(item.get("text"), str) else None,
        reading_order=order.get(source_id) if resolved else None,
        reading_order_resolved=resolved,
        decorative=decorative,
        language=None,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        extraction_method=method,
        parent_source_object_id=_ref(item.get("parent")),
        caption_for_source_object_id=caption_targets.get(source_id),
        metadata={"org.docling.label": label},
    )


def _table(item: JsonObject) -> ExtractedTable | None:
    source_id = item.get("self_ref")
    page_number = _page_number(item)
    bbox = _item_bbox(item)
    data = _object(item.get("data"))
    raw_cells = _objects(data.get("table_cells"))
    if not isinstance(source_id, str) or page_number is None or bbox is None or not raw_cells:
        return None
    cells: list[ExtractedTableCell] = []
    for index, cell in enumerate(raw_cells):
        row_start = int(cell.get("start_row_offset_idx", 0))
        col_start = int(cell.get("start_col_offset_idx", 0))
        row_end = int(cell.get("end_row_offset_idx", row_start + 1))
        col_end = int(cell.get("end_col_offset_idx", col_start + 1))
        cell_bbox = None
        if isinstance(cell.get("bbox"), Mapping):
            try:
                cell_bbox = _source_bbox(cell["bbox"])
            except (KeyError, TypeError, ValueError):
                cell_bbox = None
        cells.append(
            ExtractedTableCell(
                source_object_id=f"{source_id}/cell/{index}",
                row_index=row_start,
                column_index=col_start,
                row_span=max(1, row_end - row_start),
                column_span=max(1, col_end - col_start),
                text=str(cell.get("text", "")),
                is_header=bool(cell.get("column_header") or cell.get("row_header")),
                bbox=cell_bbox,
                confidence=None,
            )
        )
    captions = tuple(
        ref for caption in _objects(item.get("captions")) if (ref := _ref(caption)) is not None
    )
    return ExtractedTable(
        source_object_id=source_id,
        page_number=page_number,
        bbox=bbox,
        row_count=max(1, int(data.get("num_rows", 1))),
        column_count=max(1, int(data.get("num_cols", 1))),
        cells=tuple(cells),
        caption_source_object_ids=captions,
        confidence=None,
    )


def map_docling_document(
    payload: JsonObject,
    *,
    descriptor: ParserDescriptor,
    run: ParserRun,
    pages_requested: tuple[int, ...],
    status: ParseStatus = ParseStatus.SUCCESS,
) -> ParseResult:
    """Convert Docling's private wire shape to stable neutral records."""

    collections = (
        _objects(payload.get("texts"))
        + _objects(payload.get("pictures"))
        + _objects(payload.get("tables"))
        + _objects(payload.get("groups"))
    )
    indexed = {
        str(item["self_ref"]): item for item in collections if isinstance(item.get("self_ref"), str)
    }
    body_refs = _expand_refs(_object(payload.get("body")), indexed)
    order = _reading_order(body_refs, indexed)
    caption_targets = _caption_targets(payload)
    elements = [
        element
        for item in (
            _objects(payload.get("texts"))
            + _objects(payload.get("pictures"))
            + _objects(payload.get("tables"))
        )
        if (element := _element(item, order=order, caption_targets=caption_targets)) is not None
    ]
    tables = [
        table for item in _objects(payload.get("tables")) if (table := _table(item)) is not None
    ]

    raw_pages = payload.get("pages")
    page_entries = _object(raw_pages)
    warnings: list[str] = []
    pages: list[PageParseResult] = []
    for page_number in pages_requested:
        page_data = _object(page_entries.get(str(page_number)))
        size = _object(page_data.get("size"))
        if not size:
            warnings.append(f"page {page_number}: missing Docling page geometry")
            continue
        page_elements = sorted(
            (element for element in elements if element.page_number == page_number),
            key=lambda element: (
                element.reading_order is None,
                element.reading_order if element.reading_order is not None else 0,
                element.source_object_id,
            ),
        )
        rotation_value = int(page_data.get("rotation", 0) or 0) % 360
        if rotation_value not in {0, 90, 180, 270}:
            warnings.append(f"page {page_number}: invalid rotation {rotation_value}")
            continue
        rotation = cast(Literal[0, 90, 180, 270], rotation_value)
        pages.append(
            PageParseResult(
                page_number=page_number,
                width=float(size["width"]),
                height=float(size["height"]),
                rotation=rotation,
                coordinate_unit=CoordinateUnit.POINT,
                elements=tuple(page_elements),
                tables=tuple(table for table in tables if table.page_number == page_number),
                warnings=(),
            )
        )
    missing_items = len(collections) - len(elements) - len(_objects(payload.get("groups")))
    if missing_items > 0:
        warnings.append(f"{missing_items} Docling items lacked usable page geometry")
    return ParseResult(
        status=status if len(pages) == len(pages_requested) else ParseStatus.PARTIAL,
        descriptor=descriptor,
        run=run,
        pages_requested=pages_requested,
        pages=tuple(pages),
        warnings=tuple(warnings),
        errors=(),
    )

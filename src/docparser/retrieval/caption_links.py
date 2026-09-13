"""Conservative, query-independent caption associations; never Canonical facts."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from docparser.ir.base import StrictIRModel
from docparser.ir.enums import BlockType
from docparser.ir.models import DocumentIR

CAPTION_LINK_VERSION = "caption-links@1.0.0"
_TABLE_LABEL = re.compile(r"^\s*(?:(?:Table|Tab\.)\s*\d+|表\s*[0-9一二三四五六七八九十]+)", re.I)


class CaptionLink(StrictIRModel):
    target_source_id: str
    basis: Literal["EXPLICIT", "DERIVED_UNIQUE_GEOMETRY"]
    version: str = CAPTION_LINK_VERSION
    gap_points: float | None
    horizontal_overlap: float | None


def associate_table_captions(
    document: DocumentIR, available: frozenset[str]
) -> dict[str, tuple[CaptionLink, ...]]:
    """Accept mutual uniqueness, not a nearest-neighbour guess or query-dependent repair."""
    links: dict[str, list[CaptionLink]] = defaultdict(list)
    explicit = {str(t.table_id): set(map(str, t.caption_block_ids)) for t in document.tables}
    reserved = set().union(*explicit.values()) if explicit else set()
    for page in document.pages:
        blocks = {str(b.block_id): b for b in page.blocks if str(b.block_id) in available}
        tables = [b for b in blocks.values() if b.block_type is BlockType.TABLE]
        candidates: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        owners: dict[str, list[str]] = defaultdict(list)

        def add(
            caption: str,
            table: str,
            basis: Literal["EXPLICIT", "DERIVED_UNIQUE_GEOMETRY"],
            gap: float | None,
            overlap: float | None,
        ) -> None:
            links[caption].append(
                CaptionLink(
                    target_source_id=table, basis=basis, gap_points=gap, horizontal_overlap=overlap
                )
            )
            links[table].append(
                CaptionLink(
                    target_source_id=caption,
                    basis=basis,
                    gap_points=gap,
                    horizontal_overlap=overlap,
                )
            )

        for table in tables:
            table_id = str(table.block_id)
            known = explicit.get(str(table.content_ref), set())
            if known:
                for explicit_caption in sorted(known & blocks.keys()):
                    add(explicit_caption, table_id, "EXPLICIT", None, None)
                continue
            for caption_id, caption in blocks.items():
                if caption_id in reserved or caption.block_type is not BlockType.FIGURE_CAPTION:
                    continue
                if not _TABLE_LABEL.match(caption.text or ""):
                    continue
                a, b = caption.bbox, table.bbox
                overlap = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0)) / min(a.width, b.width)
                gap = max(a.y0 - b.y1, b.y0 - a.y1)
                if gap < 0 or overlap < 0.5 or gap > min(3 * a.height, page.height * 0.05):
                    continue
                candidates[caption_id].append((table_id, gap, overlap))
                owners[table_id].append(caption_id)
        for caption_key, options in sorted(candidates.items()):
            if len(options) == 1:
                table_id, gap, overlap = options[0]
                if len(owners[table_id]) == 1:
                    add(caption_key, table_id, "DERIVED_UNIQUE_GEOMETRY", gap, overlap)
    return {
        key: tuple(sorted(values, key=lambda x: x.target_source_id))
        for key, values in links.items()
    }

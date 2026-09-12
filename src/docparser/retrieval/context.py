"""Budgeted source context for Fixed retrieval; no changes to embedding inputs or scores."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import Field

from docparser.ir.base import StrictIRModel
from docparser.ir.chunks import Chunk
from docparser.ir.enums import BlockType, ReadingOrderStatus, RelationshipType, TableCellHeaderRole
from docparser.ir.geometry import BBox
from docparser.ir.models import DocumentIR
from docparser.retrieval.chunking import Tokenizer, _render_block, _render_table_row
from docparser.retrieval.dense import QueryRetrieval
from docparser.retrieval.table_context import TableRowMap, TableSourceMap, build_table_source_maps

CONTEXT_VERSION = "source-context@1.1.0"


class SourceLocation(StrictIRModel):
    page_number: int = Field(ge=1)
    bbox: BBox
    precision: Literal["BLOCK", "TABLE_REGION"]


class EvidenceSource(StrictIRModel):
    source_id: str
    document_id: str
    document_name: str
    source_digest: str
    block_ids: tuple[str, ...]
    entity_id: str | None = None
    provenance_ids: tuple[str, ...]
    locations: tuple[SourceLocation, ...]
    kind: str
    text: str
    ordered: bool
    related_source_ids: tuple[str, ...] = ()
    required_context_source_ids: tuple[str, ...] = ()
    table_map: TableSourceMap | None = None
    warnings: tuple[str, ...] = ()


class SourceSpan(StrictIRModel):
    source_id: str
    token_start: int = Field(ge=0)
    token_end: int = Field(gt=0)


class ContextConfig(StrictIRModel):
    max_tokens: int = Field(default=4096, ge=1)
    max_excerpt_tokens: int = Field(default=512, ge=1)
    expand_source_tokens: int = Field(default=768, ge=0)
    include_related: bool = True
    table_policy: Literal["SOURCE_SPANS", "LOGICAL_ROWS"] = "SOURCE_SPANS"


class ContextEvidence(StrictIRModel):
    evidence_id: str
    source_id: str
    text: str
    token_start: int
    token_end: int
    complete_source: bool
    role: Literal["RETRIEVED", "EXPANDED", "RELATED"]
    retrieval_rank: int
    warnings: tuple[str, ...] = ()

    # Character intervals refer to the unchanged rendered source, not PDF bytes.
    char_start: int | None = None
    char_end: int | None = None
    row_indices: tuple[int, ...] = ()
    covered_cell_ids: tuple[str, ...] = ()
    segment_ids: tuple[str, ...] = ()
    row_band_complete: bool | None = None
    locations: tuple[SourceLocation, ...] = ()


class EvidenceContext(StrictIRModel):
    version: str = CONTEXT_VERSION
    config: ContextConfig
    tokenizer_id: str
    budget_tokens: int
    token_count: int
    text: str
    evidence: tuple[ContextEvidence, ...]
    sources: tuple[EvidenceSource, ...]
    omitted_source_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def prepare_sources(
    document: DocumentIR, tokenizer: Tokenizer, chunks: tuple[Chunk, ...]
) -> tuple[dict[str, EvidenceSource], dict[str, tuple[SourceSpan, ...]]]:
    """Resolve source intervals once during indexing, using the frozen Fixed stream encoding."""
    from docparser.retrieval.chunking import retrieval_evidence_view

    view = retrieval_evidence_view(document)
    all_blocks = {str(block.block_id): block for page in document.pages for block in page.blocks}
    tables = {str(table.table_id): table for table in document.tables}
    figures = {str(figure.figure_id): figure for figure in document.figures}
    headings = {
        str(block_id): str(section.heading_block_id)
        for section in document.sections
        if section.heading_block_id is not None
        for block_id in section.content_block_ids
    }
    sources: dict[str, EvidenceSource] = {}
    ranges: dict[str, tuple[int, int]] = {}
    offset = 0
    footnotes: dict[str, list[str]] = defaultdict(list)
    for relation in document.relationships:
        if relation.type is RelationshipType.FOOTNOTE_OF:
            footnotes[str(relation.target_id)].append(str(relation.source_id))
    for block in (*view.ordered_blocks, *view.isolated_unresolved_blocks):
        source_id = str(block.block_id)
        ordered = block.reading_order_status is ReadingOrderStatus.IN_FLOW
        text = _render_block(document, block)
        if not ordered:
            text = text.strip()
        warnings: list[str] = []
        recovery = block.extensions.get("org.docparser.recovery")
        if isinstance(recovery, dict):
            warnings.append(f"STRUCTURE_UNCERTAIN: {recovery.get('reason')}")
        locations: tuple[SourceLocation, ...] = (
            SourceLocation(page_number=block.page_number, bbox=block.bbox, precision="BLOCK"),
        )
        related: list[str] = []
        required: list[str] = []
        block_ids: tuple[str, ...] = (source_id,)
        provenance_ids = tuple(str(value) for value in block.provenance_ids)
        if block.block_type is BlockType.TABLE and str(block.content_ref) in tables:
            table = tables[str(block.content_ref)]
            segments = (
                tuple(segment for segment in table.segments if segment.block_id == block.block_id)
                or table.segments
            )
            locations = tuple(
                SourceLocation(
                    page_number=segment.page_number, bbox=segment.bbox, precision="TABLE_REGION"
                )
                for segment in segments
            )
            block_ids = tuple(str(segment.block_id) for segment in segments)
            if len(segments) < len(table.segments):
                warnings.append("PARTIAL_TABLE_SEGMENT: other rows are on another page")
            provenance_ids = tuple(
                dict.fromkeys(
                    str(value)
                    for ids in (
                        table.provenance_ids,
                        *(segment.provenance_ids for segment in segments),
                    )
                    for value in ids
                )
            )
            related.extend(str(value) for value in table.caption_block_ids)
            required.extend(str(value) for value in table.caption_block_ids)
            required.extend(footnotes[str(table.table_id)])
            required.extend(footnotes[source_id])
            header_cells = tuple(cell for cell in table.cells if cell.is_header)
            if header_cells:
                header_id = f"{table.table_id}:headers"
                header_rows = sorted({cell.row_index for cell in header_cells})
                header_pages = {
                    cell.page_number for cell in table.cells if cell.row_index in header_rows
                }
                header_warnings = (
                    ("HEADER_ROLES_UNKNOWN: preserve raw cells; do not infer column roles",)
                    if any(cell.header_role is TableCellHeaderRole.UNKNOWN for cell in header_cells)
                    else ()
                )
                header_segments = tuple(
                    segment for segment in table.segments if segment.page_number in header_pages
                )
                sources[header_id] = EvidenceSource(
                    source_id=header_id,
                    document_id=str(document.document_id),
                    document_name=document.source.original_filename_safe,
                    source_digest=str(document.source.sha256),
                    block_ids=tuple(str(segment.block_id) for segment in header_segments),
                    entity_id=str(table.table_id),
                    provenance_ids=tuple(
                        str(value) for cell in header_cells for value in cell.provenance_ids
                    ),
                    locations=tuple(
                        SourceLocation(
                            page_number=segment.page_number,
                            bbox=segment.bbox,
                            precision="TABLE_REGION",
                        )
                        for segment in header_segments
                    ),
                    kind="TABLE_HEADER_ROWS",
                    text="\n".join(_render_table_row(table, row) for row in header_rows),
                    ordered=False,
                    warnings=header_warnings,
                )
                related.append(header_id)
        elif block.block_type is BlockType.FIGURE and str(block.content_ref) in figures:
            related.extend(
                str(value) for value in figures[str(block.content_ref)].caption_block_ids
            )
        if source_id in headings:
            related.append(headings[source_id])
        sources[source_id] = EvidenceSource(
            source_id=source_id,
            document_id=str(document.document_id),
            document_name=document.source.original_filename_safe,
            source_digest=str(document.source.sha256),
            block_ids=block_ids,
            entity_id=str(block.content_ref) if block.content_ref else None,
            provenance_ids=provenance_ids,
            locations=locations,
            kind=block.block_type.value,
            text=text,
            ordered=ordered,
            related_source_ids=tuple(dict.fromkeys(related)),
            required_context_source_ids=tuple(dict.fromkeys(required)),
            warnings=tuple(warnings),
        )
        tokens = tokenizer.encode(text + ("\n\n" if ordered else ""))
        ranges[source_id] = (offset, offset + len(tokens)) if ordered else (0, len(tokens))
        if ordered:
            offset += len(tokens)
    # Related captions/headings are source observations, never synthesized descriptions.
    for source in tuple(sources.values()):
        for identifier in (*source.related_source_ids, *source.required_context_source_ids):
            if identifier in sources or identifier not in all_blocks:
                continue
            block = all_blocks[identifier]
            if not (block.text or "").strip():
                continue
            sources[identifier] = EvidenceSource(
                source_id=identifier,
                document_id=str(document.document_id),
                document_name=document.source.original_filename_safe,
                source_digest=str(document.source.sha256),
                block_ids=(identifier,),
                provenance_ids=tuple(str(value) for value in block.provenance_ids),
                locations=(
                    SourceLocation(
                        page_number=block.page_number, bbox=block.bbox, precision="BLOCK"
                    ),
                ),
                kind=block.block_type.value,
                text=block.text or "",
                ordered=False,
            )
    table_sources: dict[str, dict[str, tuple[str, bool]]] = defaultdict(dict)
    for source in sources.values():
        if source.kind == "TABLE" and source.entity_id in tables:
            table_sources[source.entity_id][source.source_id] = (source.text, source.ordered)
    for table_id, texts in table_sources.items():
        for identifier, mapping in build_table_source_maps(
            tables[table_id], texts, tokenizer
        ).items():
            sources[identifier] = sources[identifier].model_copy(update={"table_map": mapping})
    spans: dict[str, tuple[SourceSpan, ...]] = {}
    for chunk in chunks:
        if not chunk.embedding_eligible:
            continue
        if chunk.metadata.get("policy") != "FIXED_TOKEN":
            raise ValueError("source context currently requires Fixed-token retrieval chunks")
        start, end = chunk.metadata["token_start"], chunk.metadata["token_end"]
        assert isinstance(start, int) and isinstance(end, int)
        covered: list[SourceSpan] = []
        for identifier in chunk.source_block_ids:
            source_id = str(identifier)
            source_start, source_end = ranges[source_id]
            left, right = max(start, source_start), min(end, source_end)
            if left < right:
                covered.append(
                    SourceSpan(
                        source_id=source_id,
                        token_start=left - source_start,
                        token_end=right - source_start,
                    )
                )
        spans[str(chunk.chunk_id)] = tuple(covered)
    return sources, spans


@dataclass(frozen=True, slots=True)
class _Piece:
    source_id: str
    start: int
    end: int
    rank: int
    role: Literal["RETRIEVED", "EXPANDED", "RELATED"] = "RETRIEVED"
    char_start: int | None = None
    char_end: int | None = None
    rows: tuple[TableRowMap, ...] = ()
    row_band_complete: bool | None = None
    warnings: tuple[str, ...] = ()


def _merge_pieces(pieces: list[_Piece]) -> list[_Piece]:
    """Deduplicate source intervals, never text strings or shared table IDs."""
    groups: dict[str, list[_Piece]] = defaultdict(list)
    for piece in pieces:
        groups[piece.source_id].append(piece)
    result: list[_Piece] = []
    for values in groups.values():
        rows = sorted((value for value in values if value.rows), key=lambda value: value.start)
        merged: list[_Piece] = []
        for piece in rows:
            if (
                merged
                and piece.char_start is not None
                and merged[-1].char_end is not None
                and piece.char_start <= merged[-1].char_end + 1
            ):
                previous = merged.pop()
                by_row = {row.row_index: row for row in (*previous.rows, *piece.rows)}
                merged.append(
                    replace(
                        previous,
                        end=max(previous.end, piece.end),
                        char_end=max(previous.char_end or 0, piece.char_end or 0),
                        rank=min(previous.rank, piece.rank),
                        rows=tuple(sorted(by_row.values(), key=lambda row: row.char_start)),
                        row_band_complete=previous.row_band_complete and piece.row_band_complete,
                        warnings=tuple(dict.fromkeys((*previous.warnings, *piece.warnings))),
                    )
                )
            else:
                merged.append(piece)
        # Raw budget fallbacks may overlap a restored band. Keep only uncovered intervals.
        raw: list[_Piece] = []
        for piece in sorted(
            (value for value in values if not value.rows), key=lambda value: value.start
        ):
            fragments = [(piece.start, piece.end)]
            for existing in (*merged, *raw):
                fragments = [
                    interval
                    for start, end in fragments
                    for interval in (
                        (start, min(end, existing.start)),
                        (max(start, existing.end), end),
                    )
                    if interval[0] < interval[1]
                ]
            raw.extend(replace(piece, start=start, end=end) for start, end in fragments)
        joined: list[_Piece] = []
        for piece in sorted(raw, key=lambda value: value.start):
            if joined and piece.start <= joined[-1].end:
                previous = joined.pop()
                joined.append(
                    replace(
                        previous,
                        end=max(previous.end, piece.end),
                        rank=min(previous.rank, piece.rank),
                        warnings=tuple(dict.fromkeys((*previous.warnings, *piece.warnings))),
                    )
                )
            else:
                joined.append(piece)
        result.extend(sorted((*merged, *joined), key=lambda value: value.start))
    return result


class ContextBuilder:
    """Reuse tokenized source records across queries; never rescan the full DocumentIR."""

    def __init__(self, sources: dict[str, EvidenceSource], tokenizer: Tokenizer) -> None:
        self.sources = sources
        self.tokenizer = tokenizer
        self._tokens = {
            key: tokenizer.encode(source.text + ("\n\n" if source.ordered else ""))
            for key, source in sources.items()
        }
        self._row_starts = {
            key: tuple(row.token_start for row in source.table_map.rows)
            for key, source in sources.items()
            if source.table_map is not None
        }
        self._row_ends = {
            key: tuple(row.token_end for row in source.table_map.rows)
            for key, source in sources.items()
            if source.table_map is not None
        }
        self._rows = {
            key: {row.row_index: row for row in source.table_map.rows}
            for key, source in sources.items()
            if source.table_map is not None
            and source.table_map.alignment == "ALIGNED"
            and source.table_map.tokenizer_id == tokenizer.tokenizer_id
        }

    def _render(self, pieces: list[_Piece]) -> tuple[str, tuple[ContextEvidence, ...]]:
        items: list[ContextEvidence] = []
        sections: list[str] = []
        for number, piece in enumerate(pieces, start=1):
            source = self.sources[piece.source_id]
            complete = piece.start == 0 and piece.end == len(self._tokens[piece.source_id])
            if piece.char_start is not None:
                complete = piece.char_start == 0 and piece.char_end == len(source.text)
                text = source.text[piece.char_start : piece.char_end]
            else:
                text = (
                    source.text
                    if complete
                    else self.tokenizer.decode(
                        self._tokens[piece.source_id][piece.start : piece.end]
                    )
                ).strip()
            if not text:
                continue
            warnings = tuple(dict.fromkeys((*source.warnings, *piece.warnings)))
            if source.kind == "TABLE" and not complete and piece.char_start is None:
                warnings += ("INCOMPLETE_TABLE_EXCERPT: rows may be cut at token boundaries",)
            locations = source.locations
            if piece.rows and source.table_map is not None:
                segment_ids = {identifier for row in piece.rows for identifier in row.segment_ids}
                locations = tuple(
                    SourceLocation(
                        page_number=segment.page_number, bbox=segment.bbox, precision="TABLE_REGION"
                    )
                    for segment in source.table_map.segments
                    if str(segment.segment_id) in segment_ids
                )
            identifier = f"E{number}"
            pages = ",".join(
                str(page) for page in sorted({location.page_number for location in locations})
            )
            label = f"[{identifier}] {source.document_name}; pages={pages}; kind={source.kind}"
            if source.entity_id:
                label += f"; entity={source.entity_id}"
            if warnings:
                label += "\nWarnings: " + "; ".join(warnings)
            sections.append(f"{label}\n{text}")
            items.append(
                ContextEvidence(
                    evidence_id=identifier,
                    source_id=piece.source_id,
                    text=text,
                    token_start=piece.start,
                    token_end=piece.end,
                    complete_source=complete,
                    role=piece.role,
                    retrieval_rank=piece.rank,
                    warnings=warnings,
                    char_start=piece.char_start,
                    char_end=piece.char_end,
                    row_indices=tuple(row.row_index for row in piece.rows),
                    covered_cell_ids=tuple(
                        dict.fromkeys(
                            identifier for row in piece.rows for identifier in row.cell_ids
                        )
                    ),
                    row_band_complete=piece.row_band_complete,
                    segment_ids=tuple(
                        dict.fromkeys(
                            identifier for row in piece.rows for identifier in row.segment_ids
                        )
                    ),
                    locations=locations,
                )
            )
        return "\n\n".join(sections), tuple(items)

    def _table_pieces(self, piece: _Piece, *, whole: bool = False) -> list[_Piece]:
        source = self.sources[piece.source_id]
        mapping = source.table_map
        if (
            mapping is None
            or mapping.alignment != "ALIGNED"
            or mapping.tokenizer_id != self.tokenizer.tokenizer_id
        ):
            reason = "MAP_UNAVAILABLE" if mapping is None else mapping.alignment
            if mapping is not None and mapping.tokenizer_id != self.tokenizer.tokenizer_id:
                reason = "TOKENIZER_MISMATCH"
            return [
                replace(piece, row_band_complete=False, warnings=(f"TABLE_ALIGNMENT_{reason}",))
            ]
        left = bisect_right(self._row_ends[piece.source_id], piece.start)
        right = bisect_left(self._row_starts[piece.source_id], piece.end)
        touched = mapping.rows if whole else mapping.rows[left:right]
        if not touched:
            return [replace(piece, row_band_complete=False, warnings=("TABLE_NO_ALIGNED_ROWS",))]
        required_rows = set(mapping.column_header_rows)
        required_rows.update(
            index for row in touched for index in range(row.band_start, row.band_end)
        )
        pending = list(sorted(required_rows))
        planned: dict[str, dict[int, TableRowMap]] = defaultdict(dict)
        missing: set[int] = set()
        while pending:
            index = pending.pop()
            found = False
            for identifier in mapping.linked_source_ids:
                row = self._rows.get(identifier, {}).get(index)
                if row is None:
                    continue
                found = True
                planned[identifier][index] = row
                for adjacent in range(row.band_start, row.band_end):
                    if adjacent not in required_rows:
                        required_rows.add(adjacent)
                        pending.append(adjacent)
            if not found:
                missing.add(index)
        row_warnings: list[str] = []
        if mapping.unknown_header_roles:
            row_warnings.append("HEADER_ROLES_UNKNOWN: raw rows do not establish column meaning")
        if not mapping.column_header_rows:
            row_warnings.append("NO_EXPLICIT_COLUMN_HEADERS")
        if missing:
            row_warnings.append(
                "TABLE_ROWS_UNAVAILABLE: no aligned explicitly linked source for rows="
                + ",".join(map(str, sorted(missing)))
            )
        dependencies = tuple(
            dict.fromkeys(
                identifier
                for key in planned
                for identifier in self.sources[key].required_context_source_ids
            )
        )
        if any(identifier not in self.sources for identifier in dependencies):
            row_warnings.append("TABLE_REQUIRED_CONTEXT_UNAVAILABLE")
        pieces = [
            _Piece(
                identifier,
                row.token_start,
                row.token_end,
                piece.rank,
                "EXPANDED",
                row.char_start,
                row.char_end,
                (row,),
                not missing,
                tuple(row_warnings),
            )
            for identifier, rows in planned.items()
            for row in sorted(rows.values(), key=lambda row: row.char_start)
        ]
        pieces.extend(
            _Piece(identifier, 0, len(self._tokens[identifier]), piece.rank, "RELATED")
            for identifier in dependencies
            if identifier in self.sources
        )
        return _merge_pieces(pieces)

    def _build_rows(
        self, core: list[_Piece], config: ContextConfig, warnings: tuple[str, ...]
    ) -> EvidenceContext:
        selected: list[_Piece] = []
        omitted: list[str] = []
        events: list[str] = []

        def fits(candidate: list[_Piece]) -> bool:
            return len(self.tokenizer.encode(self._render(candidate)[0])) <= config.max_tokens

        # A table's rows and explicit conditions are one budget decision, ahead of topic context.
        for piece in core:
            is_table = self.sources[piece.source_id].kind == "TABLE"
            bundle = self._table_pieces(piece) if is_table else [piece]
            candidate = _merge_pieces([*selected, *bundle])
            if fits(candidate):
                selected = candidate
                continue
            if is_table:
                events.append("TABLE_RESTORATION_BUDGET_EXCEEDED")
                fallback = replace(
                    piece,
                    row_band_complete=False,
                    warnings=(
                        "TABLE_RESTORATION_BUDGET_EXCEEDED: row/header/condition bundle omitted",
                    ),
                )
                candidate = _merge_pieces([*selected, fallback])
                if fits(candidate):
                    selected = candidate
                    continue
            omitted.append(piece.source_id)
        # Full short sources are optional; do not displace another retrieved evidence bundle.
        for identifier in tuple(dict.fromkeys(piece.source_id for piece in selected)):
            count = len(self._tokens[identifier])
            if count > config.expand_source_tokens:
                continue
            matching = [piece for piece in selected if piece.source_id == identifier]
            expanded = _Piece(
                identifier, 0, count, min(piece.rank for piece in matching), "EXPANDED"
            )
            bundle = (
                self._table_pieces(expanded, whole=True)
                if self.sources[identifier].kind == "TABLE"
                else [expanded]
            )
            if self.sources[identifier].kind == "TABLE" and not any(piece.rows for piece in bundle):
                continue
            first = next(i for i, piece in enumerate(selected) if piece.source_id == identifier)
            candidate = [piece for piece in selected if piece.source_id != identifier]
            candidate[first:first] = bundle
            candidate = _merge_pieces(candidate)
            if fits(candidate):
                selected = candidate
                omitted = [value for value in omitted if value != identifier]
        if config.include_related:
            seen = {piece.source_id for piece in selected}
            for piece in tuple(selected):
                for identifier in self.sources[piece.source_id].related_source_ids:
                    if (
                        identifier in seen
                        or identifier not in self.sources
                        or self.sources[identifier].kind == "TABLE_HEADER_ROWS"
                    ):
                        continue
                    seen.add(identifier)
                    count = len(self._tokens[identifier])
                    related = _Piece(identifier, 0, count, piece.rank, "RELATED")
                    if count <= config.expand_source_tokens and fits([*selected, related]):
                        selected.append(related)
                    else:
                        omitted.append(identifier)
        text, evidence = self._render(selected)
        return EvidenceContext(
            config=config,
            tokenizer_id=self.tokenizer.tokenizer_id,
            budget_tokens=config.max_tokens,
            token_count=len(self.tokenizer.encode(text)),
            text=text,
            evidence=evidence,
            sources=tuple(
                self.sources[key] for key in dict.fromkeys(item.source_id for item in evidence)
            ),
            omitted_source_ids=tuple(dict.fromkeys(omitted)),
            warnings=tuple(
                dict.fromkeys(
                    (*warnings, *events, *(("CONTEXT_BUDGET_OMISSIONS",) if omitted else ()))
                )
            ),
        )

    def build(
        self,
        retrieval: QueryRetrieval,
        spans: dict[str, tuple[SourceSpan, ...]],
        config: ContextConfig | None = None,
        *,
        warnings: tuple[str, ...] = (),
    ) -> EvidenceContext:
        config = config or ContextConfig()
        intervals: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        for hit in retrieval.hits:
            for span in spans[str(hit.chunk_id)]:
                intervals[span.source_id].append((span.token_start, span.token_end, hit.rank))
        core: list[_Piece] = []
        for identifier, values in intervals.items():
            merged: list[tuple[int, int, int]] = []
            for start, end, rank in sorted(values):
                if merged and start <= merged[-1][1]:
                    old_start, old_end, old_rank = merged[-1]
                    merged[-1] = (old_start, max(old_end, end), min(old_rank, rank))
                else:
                    merged.append((start, end, rank))
            for start, end, rank in merged:
                for left in range(start, end, config.max_excerpt_tokens):
                    core.append(
                        _Piece(identifier, left, min(left + config.max_excerpt_tokens, end), rank)
                    )
        source_order = {identifier: index for index, identifier in enumerate(intervals)}
        core.sort(key=lambda piece: (piece.rank, source_order[piece.source_id], piece.start))
        if config.table_policy == "LOGICAL_ROWS":
            return self._build_rows(core, config, warnings)
        selected: list[_Piece] = []
        omitted: list[str] = []

        def fits(candidate: list[_Piece]) -> bool:
            return len(self.tokenizer.encode(self._render(candidate)[0])) <= config.max_tokens

        # Reserve the budget for retrieved evidence before optional expansions.
        for piece in core:
            if fits([*selected, piece]):
                selected.append(piece)
            else:
                omitted.append(piece.source_id)
        for identifier in dict.fromkeys(piece.source_id for piece in selected):
            count = len(self._tokens[identifier])
            if count > config.expand_source_tokens:
                continue
            matching = [piece for piece in selected if piece.source_id == identifier]
            first = next(i for i, piece in enumerate(selected) if piece.source_id == identifier)
            expanded = _Piece(
                identifier, 0, count, min(piece.rank for piece in matching), "EXPANDED"
            )
            candidate = [piece for piece in selected if piece.source_id != identifier]
            candidate.insert(first, expanded)
            if fits(candidate):
                selected = candidate
                omitted = [value for value in omitted if value != identifier]
        if config.include_related:
            seen = {piece.source_id for piece in selected}
            for piece in tuple(selected):
                source = self.sources[piece.source_id]
                for identifier in source.related_source_ids:
                    if identifier in seen or identifier not in self.sources:
                        continue
                    related = self.sources[identifier]
                    # A complete table already includes its header rows.
                    if (
                        related.kind == "TABLE_HEADER_ROWS"
                        and piece.start == 0
                        and (piece.end == len(self._tokens[piece.source_id]))
                        and related.text.strip() in source.text
                    ):
                        seen.add(identifier)
                        continue
                    count = len(self._tokens[identifier])
                    related_piece = _Piece(identifier, 0, count, piece.rank, "RELATED")
                    seen.add(identifier)
                    if count <= config.expand_source_tokens and fits([*selected, related_piece]):
                        selected.append(related_piece)
                    else:
                        omitted.append(identifier)
        text, evidence = self._render(selected)
        source_ids = tuple(dict.fromkeys(item.source_id for item in evidence))
        return EvidenceContext(
            config=config,
            tokenizer_id=self.tokenizer.tokenizer_id,
            budget_tokens=config.max_tokens,
            token_count=len(self.tokenizer.encode(text)),
            text=text,
            evidence=evidence,
            sources=tuple(self.sources[key] for key in source_ids),
            omitted_source_ids=tuple(dict.fromkeys(omitted)),
            warnings=warnings + (("CONTEXT_BUDGET_OMISSIONS",) if omitted else ()),
        )

"""Deterministic fixed-token and structure-aware retrieval chunks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from docparser.ir.base import StrictIRModel
from docparser.ir.chunks import Chunk, ChunkBBox
from docparser.ir.enums import (
    RETRIEVAL_FLOW_BLOCK_TYPES,
    BlockType,
    ChunkType,
    ReadingOrderStatus,
    TableCellHeaderRole,
)
from docparser.ir.ids import (
    BlockId,
    ChunkId,
    ContentEntityId,
    ProvenanceId,
    SectionId,
    generate_uuid5_id,
)
from docparser.ir.models import Block, DocumentIR
from docparser.ir.tables import Table, TableCell, TableSegment
from docparser.ir.types import Sha256Digest

FIXED_CHUNKER_VERSION = "ir-fixed-token@1.1.0"
STRUCTURE_CHUNKER_VERSION = "ir-structure-aware@2.1.0"
STRUCTURE_EMBEDDING_TOKEN_LIMIT = 8000


class ChunkingError(ValueError):
    """Raised when an IR cannot be chunked without violating the selected policy."""


class Tokenizer(Protocol):
    @property
    def tokenizer_id(self) -> str: ...

    def encode(self, text: str) -> tuple[int, ...]: ...

    def decode(self, token_ids: Sequence[int]) -> str: ...


class FixedChunkConfig(StrictIRModel):
    target_tokens: int = Field(default=512, strict=True, ge=1)
    overlap_tokens: int = Field(default=64, strict=True, ge=0)

    @model_validator(mode="after")
    def _validate_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("fixed overlap_tokens must be smaller than target_tokens")
        return self


class StructureChunkConfig(StrictIRModel):
    target_tokens: int = Field(default=512, strict=True, ge=1)
    hard_max_tokens: int = Field(
        default=STRUCTURE_EMBEDDING_TOKEN_LIMIT,
        strict=True,
        ge=1,
        le=STRUCTURE_EMBEDDING_TOKEN_LIMIT,
    )
    semantic_overlap_units: int = Field(default=1, strict=True, ge=0)

    @model_validator(mode="after")
    def _validate_limits(self) -> Self:
        if self.hard_max_tokens < self.target_tokens:
            raise ValueError("structure hard_max_tokens must be >= target_tokens")
        return self


@dataclass(frozen=True, slots=True)
class _SemanticRetrievalUnit:
    text: str
    blocks: tuple[Block, ...]
    semantic_type: BlockType
    protected_boundary: bool = False
    overlap_eligible: bool = True
    chunk_type: ChunkType = ChunkType.CHILD
    table: Table | None = None
    row_indices: tuple[int, ...] = ()
    repeated_header_rows: tuple[int, ...] = ()
    context_blocks: tuple[Block, ...] = ()
    extra_provenance_ids: tuple[ProvenanceId, ...] = ()
    oversized_split: bool = False
    table_header_aware: bool = False
    segment_bboxes: tuple[ChunkBBox, ...] = ()
    table_segment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalEvidenceView:
    """Renderable semantic evidence partitioned by reading-order confidence."""

    ordered_blocks: tuple[Block, ...]
    isolated_unresolved_blocks: tuple[Block, ...]
    unrenderable_blocks: tuple[Block, ...]

    @property
    def expected_source_block_ids(self) -> frozenset[BlockId]:
        return frozenset(
            block.block_id
            for block in (*self.ordered_blocks, *self.isolated_unresolved_blocks)
        )


def _sha256(value: str | bytes) -> Sha256Digest:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return Sha256Digest(f"sha256:{hashlib.sha256(raw).hexdigest()}")


def _config_hash(config: StrictIRModel) -> Sha256Digest:
    encoded = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _namespace(document: DocumentIR) -> UUID:
    return UUID(str(document.document_id).removeprefix("doc_"))


def retrieval_evidence_view(document: DocumentIR) -> RetrievalEvidenceView:
    """Build the complete retrieval inventory without inventing reading order."""

    ordered: list[Block] = []
    unresolved: list[Block] = []
    unrenderable: list[Block] = []
    for page in sorted(document.pages, key=lambda item: item.page_number):
        candidates = [
            block
            for block in page.blocks
            if (
                block.block_type in RETRIEVAL_FLOW_BLOCK_TYPES
                or (
                    block.block_type is BlockType.UNKNOWN
                    and isinstance(recovery := block.extensions.get("org.docparser.recovery"), dict)
                    and recovery.get("retrievable_text") is True
                    and block.reading_order_status is ReadingOrderStatus.UNRESOLVED
                )
            )
            and block.reading_order_status
            in {ReadingOrderStatus.IN_FLOW, ReadingOrderStatus.UNRESOLVED}
        ]
        renderable: list[Block] = []
        for block in candidates:
            if _render_block(document, block).strip():
                renderable.append(block)
            else:
                unrenderable.append(block)
        ordered.extend(
            sorted(
                (
                    block
                    for block in renderable
                    if block.reading_order_status is ReadingOrderStatus.IN_FLOW
                ),
                key=lambda block: block.reading_order or 0,
            )
        )
        unresolved.extend(
            block
            for block in renderable
            if block.reading_order_status is ReadingOrderStatus.UNRESOLVED
        )
    return RetrievalEvidenceView(
        ordered_blocks=tuple(ordered),
        isolated_unresolved_blocks=tuple(
            sorted(unresolved, key=lambda block: (block.page_number, str(block.block_id)))
        ),
        unrenderable_blocks=tuple(
            sorted(unrenderable, key=lambda block: (block.page_number, str(block.block_id)))
        ),
    )


def covered_source_block_ids(chunks: Iterable[Chunk]) -> frozenset[BlockId]:
    """Return every source block represented directly or as rendered context."""

    covered: set[BlockId] = set()
    for chunk in chunks:
        if not chunk.embedding_eligible:
            continue
        covered.update(chunk.source_block_ids)
        for key in ("context_source_block_ids", "overlap_source_block_ids"):
            values = chunk.metadata.get(key, [])
            assert isinstance(values, list)
            for value in values:
                assert isinstance(value, str)
                covered.add(BlockId(value))
    return frozenset(covered)


def _table_by_id(document: DocumentIR) -> dict[ContentEntityId, Table]:
    return {table.table_id: table for table in document.tables}


def _block_by_id(document: DocumentIR) -> dict[BlockId, Block]:
    return {block.block_id: block for page in document.pages for block in page.blocks}


def _render_cell(cell: TableCell) -> str:
    text = cell.text.strip()
    if cell.row_span == 1 and cell.column_span == 1:
        return text
    return f"{text} [rowspan={cell.row_span} colspan={cell.column_span}]".strip()


def _render_table_row(table: Table, row_index: int) -> str:
    anchors = {cell.column_index: cell for cell in table.cells if cell.row_index == row_index}
    values = [
        _render_cell(anchors[column]) if column in anchors else ""
        for column in range(table.logical_column_count)
    ]
    return "| " + " | ".join(values) + " |"


def _render_table(table: Table, rows: Iterable[int] | None = None) -> str:
    selected_rows = rows if rows is not None else range(table.logical_row_count)
    return "\n".join(_render_table_row(table, row) for row in selected_rows)


def _render_block(document: DocumentIR, block: Block) -> str:
    if block.block_type is BlockType.TABLE and block.content_ref is not None:
        table = _table_by_id(document).get(block.content_ref)
        if table is not None:
            rows = tuple(
                row
                for segment in table.segments
                if segment.block_id == block.block_id
                for row in range(segment.row_start, segment.row_end_exclusive)
            )
            return (
                _render_table(table, tuple(dict.fromkeys(rows)))
                if rows
                else _render_table(table)
            )
    if block.block_type is BlockType.EQUATION and block.content_ref is not None:
        equation = next(
            (item for item in document.equations if item.equation_id == block.content_ref), None
        )
        if equation is not None:
            return equation.text
    return block.text or ""


def _unique_blocks(blocks: Iterable[Block]) -> tuple[Block, ...]:
    seen: set[BlockId] = set()
    result: list[Block] = []
    for block in blocks:
        if block.block_id not in seen:
            seen.add(block.block_id)
            result.append(block)
    return tuple(result)


def _unique_provenance(
    blocks: Iterable[Block],
    table: Table | None,
    extra_provenance_ids: Iterable[ProvenanceId] = (),
) -> tuple[ProvenanceId, ...]:
    values: list[ProvenanceId] = []
    seen: set[ProvenanceId] = set()
    candidates = [identifier for block in blocks for identifier in block.provenance_ids]
    if table is not None:
        candidates.extend(table.provenance_ids)
        candidates.extend(identifier for cell in table.cells for identifier in cell.provenance_ids)
    candidates.extend(extra_provenance_ids)
    for identifier in candidates:
        if identifier not in seen:
            seen.add(identifier)
            values.append(identifier)
    return tuple(values)


def _chunk(
    document: DocumentIR,
    *,
    text: str,
    blocks: Iterable[Block],
    tokenizer: Tokenizer,
    chunker_version: str,
    config_hash: Sha256Digest,
    ordinal: int,
    chunk_type: ChunkType,
    parent_chunk_id: ChunkId | None = None,
    section_id: SectionId | None = None,
    heading_path: tuple[str, ...] = (),
    table: Table | None = None,
    metadata: dict[str, JsonValue] | None = None,
    embedding_eligible: bool | None = None,
    token_count: int | None = None,
    additional_source_entity_ids: Iterable[ContentEntityId] = (),
    extra_provenance_ids: Iterable[ProvenanceId] = (),
    source_bboxes: Iterable[ChunkBBox] | None = None,
) -> Chunk:
    selected = _unique_blocks(blocks)
    if not selected:
        raise ChunkingError("a chunk must resolve to at least one source block")
    resolved_token_count = len(tokenizer.encode(text)) if token_count is None else token_count
    source_entity_ids: tuple[ContentEntityId, ...] = tuple(
        dict.fromkeys(block.content_ref for block in selected if block.content_ref is not None)
    )
    if table is not None and table.table_id not in source_entity_ids:
        source_entity_ids += (table.table_id,)
    source_entity_ids = tuple(dict.fromkeys((*source_entity_ids, *additional_source_entity_ids)))
    content_digest = _sha256(text)
    chunk_id = generate_uuid5_id(
        ChunkId,
        _namespace(document),
        str(document.revision_id),
        chunker_version,
        tokenizer.tokenizer_id,
        str(config_hash),
        chunk_type.value,
        str(parent_chunk_id),
        str(section_id),
        str(ordinal),
        *(str(block.block_id) for block in selected),
        str(content_digest),
    )
    resolved_bboxes = (
        tuple(source_bboxes)
        if source_bboxes is not None
        else tuple(
            ChunkBBox(page_number=block.page_number, bbox=block.bbox)
            for block in selected
        )
    )
    if not resolved_bboxes:
        raise ChunkingError("a chunk must resolve to at least one source bbox")
    pages = tuple(item.page_number for item in resolved_bboxes)
    return Chunk(
        chunk_id=chunk_id,
        document_id=document.document_id,
        ir_revision_id=document.revision_id,
        chunk_schema_version="1.0.0",
        chunker_version=chunker_version,
        chunk_config_hash=config_hash,
        chunk_type=chunk_type,
        parent_chunk_id=parent_chunk_id,
        text=text,
        parent_section_id=section_id,
        heading_path=heading_path,
        page_start=min(pages),
        page_end=max(pages),
        source_block_ids=tuple(block.block_id for block in selected),
        source_entity_ids=source_entity_ids,
        bboxes=resolved_bboxes,
        content_types=tuple(dict.fromkeys(block.block_type for block in selected)),
        token_count=resolved_token_count,
        tokenizer_id=tokenizer.tokenizer_id,
        content_digest=content_digest,
        embedding_input_digest=content_digest,
        embedding_eligible=(
            bool(text.strip()) if embedding_eligible is None else embedding_eligible
        ),
        sparse_eligible=bool(text.strip()),
        metadata=metadata or {},
        provenance_ids=_unique_provenance(selected, table, extra_provenance_ids),
    )


def fixed_token_chunks(
    document: DocumentIR,
    tokenizer: Tokenizer,
    config: FixedChunkConfig | None = None,
) -> tuple[Chunk, ...]:
    """Create the controlled continuous-token baseline over Canonical IR blocks."""

    config = config or FixedChunkConfig()
    evidence = retrieval_evidence_view(document)
    token_ids: list[int] = []
    ranges: list[tuple[int, int, Block]] = []
    for block in evidence.ordered_blocks:
        rendered = _render_block(document, block)
        start = len(token_ids)
        token_ids.extend(tokenizer.encode(rendered + "\n\n"))
        ranges.append((start, len(token_ids), block))

    config_hash = _config_hash(config)
    chunks: list[Chunk] = []
    ordinal = 0
    parent: Chunk | None = None
    if token_ids:
        all_blocks = tuple(block for _, _, block in ranges)
        parent = _chunk(
            document,
            text=tokenizer.decode(token_ids).rstrip(),
            blocks=all_blocks,
            tokenizer=tokenizer,
            chunker_version=FIXED_CHUNKER_VERSION,
            config_hash=config_hash,
            ordinal=ordinal,
            chunk_type=ChunkType.PARENT,
            metadata={"policy": "FIXED_TOKEN", "context_scope": "DOCUMENT"},
            embedding_eligible=False,
        )
        chunks.append(parent)
        ordinal += 1
    step = config.target_tokens - config.overlap_tokens
    for start in range(0, len(token_ids), step):
        end = min(start + config.target_tokens, len(token_ids))
        selected = tuple(
            block
            for block_start, block_end, block in ranges
            if block_start < end and block_end > start
        )
        if not selected:
            continue
        chunks.append(
            _chunk(
                document,
                text=tokenizer.decode(token_ids[start:end]).rstrip(),
                blocks=selected,
                tokenizer=tokenizer,
                chunker_version=FIXED_CHUNKER_VERSION,
                config_hash=config_hash,
                ordinal=ordinal,
                chunk_type=ChunkType.CHILD,
                parent_chunk_id=parent.chunk_id if parent is not None else None,
                metadata={
                    "policy": "FIXED_TOKEN",
                    "token_start": start,
                    "token_end": end,
                    "overlap_tokens": config.overlap_tokens,
                },
            )
        )
        ordinal += 1
        if end == len(token_ids):
            break

    for block in evidence.isolated_unresolved_blocks:
        rendered = _render_block(document, block).strip()
        isolated_tokens = tokenizer.encode(rendered)
        for start in range(0, len(isolated_tokens), step):
            end = min(start + config.target_tokens, len(isolated_tokens))
            chunks.append(
                _chunk(
                    document,
                    text=tokenizer.decode(isolated_tokens[start:end]),
                    blocks=(block,),
                    tokenizer=tokenizer,
                    chunker_version=FIXED_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    metadata={
                        "policy": "FIXED_TOKEN",
                        "reading_order_policy": "ISOLATED_UNRESOLVED",
                        "source_reading_order_status": "UNRESOLVED",
                        "token_start": start,
                        "token_end": end,
                        "overlap_tokens": config.overlap_tokens,
                    },
                )
            )
            ordinal += 1
            if end == len(isolated_tokens):
                break
    return tuple(chunks)


def _heading_prefix(heading: Block | None) -> tuple[str, tuple[str, ...]]:
    if heading is None or not (heading.text or "").strip():
        return "", ()
    value = (heading.text or "").strip()
    return f"Section: {value}\n\n", (value,)


def _caption_blocks(
    table: Table, blocks_by_id: dict[BlockId, Block]
) -> tuple[Block, ...]:
    return tuple(
        blocks_by_id[block_id]
        for block_id in table.caption_block_ids
    )


def _explicit_column_labels(table: Table) -> tuple[str, ...] | None:
    labels: list[list[str]] = [[] for _ in range(table.logical_column_count)]
    header_cells = sorted(
        (
            cell
            for cell in table.cells
            if cell.header_role
            in {TableCellHeaderRole.COLUMN_HEADER, TableCellHeaderRole.BOTH}
            and cell.text.strip()
        ),
        key=lambda cell: (cell.row_index, cell.column_index, str(cell.cell_id)),
    )
    for cell in header_cells:
        for column in range(cell.column_index, cell.column_index + cell.column_span):
            labels[column].append(cell.text.strip())
    if not labels or any(not values for values in labels):
        return None
    return tuple(" / ".join(dict.fromkeys(values)) for values in labels)


def _render_key_value_row(
    table: Table, row_index: int, column_labels: tuple[str, ...]
) -> str:
    cells = sorted(
        (cell for cell in table.cells if cell.row_index == row_index),
        key=lambda cell: (cell.column_index, str(cell.cell_id)),
    )
    lines: list[str] = []
    for cell in cells:
        labels = column_labels[
            cell.column_index : cell.column_index + cell.column_span
        ]
        key = " / ".join(dict.fromkeys(labels))
        lines.append(f"{key}: {_render_cell(cell)}")
    return "Row:\n" + "\n".join(lines)


def _render_table_rows(
    table: Table,
    rows: Sequence[int],
    column_labels: tuple[str, ...] | None,
) -> str:
    if column_labels is not None:
        return "\n\n".join(
            _render_key_value_row(table, row, column_labels) for row in rows
        )
    return "\n".join(_render_table_row(table, row) for row in rows)


def _table_context_prefix(
    section_prefix: str,
    caption_blocks: Sequence[Block],
    table: Table,
    column_labels: tuple[str, ...] | None,
    repeated_header_rows: Sequence[int],
) -> str:
    parts = [section_prefix.rstrip()]
    parts.extend(
        f"Table: {text}"
        for block in caption_blocks
        if (text := (block.text or "").strip())
    )
    if column_labels is not None:
        parts.append("Columns:\n" + " | ".join(column_labels))
    elif repeated_header_rows:
        parts.append("Headers:\n" + _render_table(table, repeated_header_rows))
    return "\n\n".join(part for part in parts if part)


def _render_table_unit(
    prefix: str,
    table: Table,
    rows: Sequence[int],
    column_labels: tuple[str, ...] | None,
) -> str:
    body = _render_table_rows(table, rows, column_labels)
    return "\n\n".join(part for part in (prefix, body) if part).strip()


def _row_bands(table: Table, data_rows: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """Keep row-spanning cells inside one indivisible row band."""

    bands: list[tuple[int, ...]] = []
    index = 0
    while index < len(data_rows):
        start = data_rows[index]
        end = start + 1
        changed = True
        while changed:
            changed = False
            for cell in table.cells:
                cell_end = cell.row_index + cell.row_span
                if cell.row_index < end and cell_end > start and cell_end > end:
                    end = cell_end
                    changed = True
        band = tuple(row for row in data_rows if start <= row < end)
        if band:
            bands.append(band)
            index += len(band)
        else:
            index += 1
    return tuple(bands)


def _segment_intersects_rows(row_start: int, row_end: int, rows: set[int]) -> bool:
    return any(row_start <= row < row_end for row in rows)


def _table_segments_for_rows(
    table: Table, rows: Sequence[int]
) -> tuple[TableSegment, ...]:
    selected_rows = set(rows)
    return tuple(
        segment
        for segment in table.segments
        if _segment_intersects_rows(
            segment.row_start, segment.row_end_exclusive, selected_rows
        )
    )


def _table_blocks_for_rows(
    table: Table,
    rows: Sequence[int],
    fallback: Block,
    blocks_by_id: dict[BlockId, Block],
) -> tuple[Block, ...]:
    blocks = tuple(
        blocks_by_id[segment.block_id]
        for segment in _table_segments_for_rows(table, rows)
    )
    return blocks or (fallback,)


def _table_provenance_for_rows(
    table: Table,
    rows: Sequence[int],
    repeated_header_rows: Sequence[int],
    context_blocks: Sequence[Block],
) -> tuple[ProvenanceId, ...]:
    relevant_rows = set((*rows, *repeated_header_rows))
    values: list[ProvenanceId] = []
    values.extend(
        identifier
        for segment in table.segments
        if _segment_intersects_rows(
            segment.row_start, segment.row_end_exclusive, set(rows)
        )
        for identifier in segment.provenance_ids
    )
    values.extend(
        identifier
        for cell in table.cells
        if any(
            cell.row_index <= row < cell.row_index + cell.row_span
            for row in relevant_rows
        )
        for identifier in cell.provenance_ids
    )
    values.extend(
        identifier for block in context_blocks for identifier in block.provenance_ids
    )
    return tuple(dict.fromkeys(values))


def _table_units(
    document: DocumentIR,
    block: Block,
    table: Table,
    heading: Block | None,
    tokenizer: Tokenizer,
    config: StructureChunkConfig,
) -> tuple[_SemanticRetrievalUnit, ...]:
    blocks_by_id = _block_by_id(document)
    section_prefix, _ = _heading_prefix(heading)
    captions = tuple(
        caption
        for caption in _caption_blocks(table, blocks_by_id)
        if caption.block_type in RETRIEVAL_FLOW_BLOCK_TYPES
        and caption.reading_order_status
        in {ReadingOrderStatus.IN_FLOW, ReadingOrderStatus.UNRESOLVED}
        and (caption.text or "").strip()
    )
    header_rows = table.header_row_indices
    data_rows = tuple(
        row for row in range(table.logical_row_count) if row not in header_rows
    )
    column_labels = _explicit_column_labels(table)
    if not data_rows:
        data_rows = tuple(range(table.logical_row_count))
        header_rows = ()
        column_labels = None
    context_prefix = _table_context_prefix(
        section_prefix,
        captions,
        table,
        column_labels,
        header_rows,
    )
    header_segments = _table_segments_for_rows(table, header_rows)
    header_blocks = tuple(blocks_by_id[segment.block_id] for segment in header_segments)
    context_blocks = _unique_blocks(
        ((heading,) if heading is not None else ()) + captions + header_blocks
    )
    bands = _row_bands(table, data_rows)
    units: list[_SemanticRetrievalUnit] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        rows = tuple(current)
        segments = _table_segments_for_rows(table, rows)
        blocks = _table_blocks_for_rows(table, rows, block, blocks_by_id)
        units.append(
            _SemanticRetrievalUnit(
                text=_render_table_unit(
                    context_prefix, table, rows, column_labels
                ),
                blocks=blocks,
                semantic_type=BlockType.TABLE,
                protected_boundary=True,
                overlap_eligible=False,
                chunk_type=ChunkType.TABLE,
                table=table,
                row_indices=rows,
                repeated_header_rows=header_rows,
                context_blocks=context_blocks,
                extra_provenance_ids=_table_provenance_for_rows(
                    table, rows, header_rows, context_blocks
                ),
                table_header_aware=column_labels is not None,
                segment_bboxes=tuple(
                    ChunkBBox(page_number=segment.page_number, bbox=segment.bbox)
                    for segment in segments
                ),
                table_segment_ids=tuple(str(segment.segment_id) for segment in segments),
            )
        )
        current.clear()

    for band in bands:
        candidate = (*current, *band)
        candidate_text = _render_table_unit(
            context_prefix, table, candidate, column_labels
        )
        candidate_tokens = len(tokenizer.encode(candidate_text))
        if current and candidate_tokens > config.target_tokens:
            flush()
            candidate = band
            candidate_text = _render_table_unit(
                context_prefix, table, candidate, column_labels
            )
            candidate_tokens = len(tokenizer.encode(candidate_text))
        if candidate_tokens > config.hard_max_tokens:
            raise ChunkingError(
                "a complete logical table row band exceeds structure hard_max_tokens"
            )
        current.extend(band)
    flush()
    return tuple(units)


def _normal_unit(document: DocumentIR, block: Block) -> _SemanticRetrievalUnit | None:
    rendered = _render_block(document, block).strip()
    if not rendered:
        return None
    protected = block.block_type in {BlockType.FIGURE, BlockType.EQUATION}
    return _SemanticRetrievalUnit(
        text=rendered,
        blocks=(block,),
        semantic_type=block.block_type,
        protected_boundary=protected,
        overlap_eligible=not protected,
    )


def _split_normal_unit(
    unit: _SemanticRetrievalUnit,
    prefix: str,
    tokenizer: Tokenizer,
    config: StructureChunkConfig,
) -> tuple[_SemanticRetrievalUnit, ...]:
    prefix_tokens = tokenizer.encode(prefix)
    capacity = config.target_tokens - len(prefix_tokens)
    if capacity <= 0:
        raise ChunkingError("section heading consumes the complete structure token budget")
    body_tokens = tokenizer.encode(unit.text)
    result: list[_SemanticRetrievalUnit] = []
    for start in range(0, len(body_tokens), capacity):
        result.append(
            _SemanticRetrievalUnit(
                text=tokenizer.decode(body_tokens[start : start + capacity]),
                blocks=unit.blocks,
                semantic_type=unit.semantic_type,
                overlap_eligible=False,
                oversized_split=True,
            )
        )
    return tuple(result)


def _render_normal_units(prefix: str, units: Sequence[_SemanticRetrievalUnit]) -> str:
    body = "\n\n".join(unit.text for unit in units)
    return (prefix + body).strip()


def _component_token_count(tokenizer: Tokenizer, parts: Sequence[str]) -> int:
    """Count a non-embedding parent without encoding one model-oversized string."""

    rendered = [part for part in parts if part]
    return sum(
        len(tokenizer.encode(part + ("\n\n" if index < len(rendered) - 1 else "")))
        for index, part in enumerate(rendered)
    )


def _overlap_tail(
    units: Sequence[_SemanticRetrievalUnit], limit: int
) -> tuple[_SemanticRetrievalUnit, ...]:
    if limit == 0:
        return ()
    selected: list[_SemanticRetrievalUnit] = []
    for unit in reversed(units):
        if not unit.overlap_eligible or unit.oversized_split:
            break
        selected.append(unit)
        if len(selected) == limit:
            break
    return tuple(reversed(selected))


def _build_table_chunk(
    document: DocumentIR,
    unit: _SemanticRetrievalUnit,
    tokenizer: Tokenizer,
    config: StructureChunkConfig,
    config_hash: Sha256Digest,
    ordinal: int,
    *,
    parent_chunk_id: ChunkId | None,
    section_id: SectionId | None,
    heading_path: tuple[str, ...],
    rendered_heading_prefix: bool,
    reading_order_policy: str | None = None,
) -> Chunk:
    assert unit.table is not None
    token_count = len(tokenizer.encode(unit.text))
    if token_count > config.hard_max_tokens:
        raise ChunkingError("a table row group exceeds structure hard_max_tokens")
    metadata: dict[str, JsonValue] = {
        "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
        "protected_unit": "TABLE",
        "rendered_heading_prefix": rendered_heading_prefix,
        "row_start": min(unit.row_indices),
        "row_end_exclusive": max(unit.row_indices) + 1,
        "data_row_indices": list(unit.row_indices),
        "repeated_header_rows": list(unit.repeated_header_rows),
        "caption_block_ids": [
            str(block.block_id)
            for block in unit.context_blocks
            if block.block_id in unit.table.caption_block_ids
        ],
        "table_segment_ids": list(unit.table_segment_ids),
        "context_source_block_ids": [
            str(block.block_id) for block in unit.context_blocks
        ],
        "overlap_source_block_ids": [],
        "table_rendering": (
            "HEADER_AWARE_KEY_VALUE"
            if unit.table_header_aware
            else "COMPACT_LOGICAL_ROWS"
        ),
    }
    if reading_order_policy is not None:
        metadata.update(
            {
                "reading_order_policy": reading_order_policy,
                "source_reading_order_status": "UNRESOLVED",
            }
        )
    return _chunk(
        document,
        text=unit.text,
        blocks=unit.blocks,
        tokenizer=tokenizer,
        chunker_version=STRUCTURE_CHUNKER_VERSION,
        config_hash=config_hash,
        ordinal=ordinal,
        chunk_type=ChunkType.TABLE,
        parent_chunk_id=parent_chunk_id,
        section_id=section_id,
        heading_path=heading_path,
        metadata=metadata,
        token_count=token_count,
        additional_source_entity_ids=(unit.table.table_id,),
        extra_provenance_ids=unit.extra_provenance_ids,
        source_bboxes=unit.segment_bboxes or None,
    )


def structure_aware_chunks(
    document: DocumentIR,
    tokenizer: Tokenizer,
    config: StructureChunkConfig | None = None,
) -> tuple[Chunk, ...]:
    """Build relationship-bound semantic retrieval chunks from materialized Sections."""

    config = config or StructureChunkConfig()
    evidence = retrieval_evidence_view(document)
    if not document.sections and not evidence.isolated_unresolved_blocks:
        raise ChunkingError("structure-aware chunking requires materialized sections")
    blocks_by_id = _block_by_id(document)
    tables_by_id = _table_by_id(document)
    config_hash = _config_hash(config)
    chunks: list[Chunk] = []
    seen_tables: set[ContentEntityId] = set()
    bound_table_caption_ids = {
        caption_id for table in document.tables for caption_id in table.caption_block_ids
    }
    ordinal = 0

    for section in document.sections:
        heading = (
            blocks_by_id.get(section.heading_block_id)
            if section.heading_block_id is not None
            else None
        )
        prefix, heading_path = _heading_prefix(heading)
        units: list[_SemanticRetrievalUnit] = []
        parent_parts: list[str] = []
        parent_count_parts: list[str] = []
        for block_id in section.content_block_ids:
            block = blocks_by_id[block_id]
            if (
                block.reading_order_status is not ReadingOrderStatus.IN_FLOW
                or block.block_type not in RETRIEVAL_FLOW_BLOCK_TYPES
            ):
                continue
            if block.block_id in bound_table_caption_ids:
                continue
            if block.block_type is BlockType.TABLE and block.content_ref is not None:
                if block.content_ref in seen_tables:
                    continue
                table = tables_by_id.get(block.content_ref)
                if table is not None:
                    seen_tables.add(block.content_ref)
                    table_units = _table_units(
                        document, block, table, heading, tokenizer, config
                    )
                    units.extend(table_units)
                    captions = _caption_blocks(table, blocks_by_id)
                    labels = _explicit_column_labels(table)
                    header_rows = table.header_row_indices
                    data_rows = tuple(
                        row
                        for row in range(table.logical_row_count)
                        if row not in header_rows
                    )
                    if not data_rows:
                        data_rows = tuple(range(table.logical_row_count))
                        header_rows = ()
                        labels = None
                    table_prefix = _table_context_prefix(
                        "", captions, table, labels, header_rows
                    )
                    parent_parts.append(
                        _render_table_unit(table_prefix, table, data_rows, labels)
                    )
                    parent_count_parts.extend(
                        (
                            table_prefix,
                            *(
                                _render_table_rows(table, (row,), labels)
                                for row in data_rows
                            ),
                        )
                    )
                    continue
            unit = _normal_unit(document, block)
            if unit is not None:
                units.append(unit)
                parent_parts.append(unit.text)
                parent_count_parts.append(unit.text)

        parent_blocks = (heading,) if heading is not None else ()
        parent_blocks += tuple(
            block for unit in units for block in (*unit.blocks, *unit.context_blocks)
        )
        parent_blocks = _unique_blocks(parent_blocks)
        if not parent_blocks:
            continue
        parent_text = (prefix + "\n\n".join(parent_parts)).strip()
        parent = _chunk(
            document,
            text=parent_text,
            blocks=parent_blocks,
            tokenizer=tokenizer,
            chunker_version=STRUCTURE_CHUNKER_VERSION,
            config_hash=config_hash,
            ordinal=ordinal,
            chunk_type=ChunkType.PARENT,
            section_id=section.section_id,
            heading_path=heading_path,
            metadata={
                "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
                "context_scope": "SECTION",
                "token_count_mode": "COMPONENT_SUM_NON_EMBEDDING",
            },
            embedding_eligible=False,
            token_count=_component_token_count(
                tokenizer, (prefix.rstrip(), *parent_count_parts)
            ),
        )
        chunks.append(parent)
        ordinal += 1
        if not units and heading is not None:
            heading_text = (heading.text or "").strip()
            chunks.append(
                _chunk(
                    document,
                    text=heading_text,
                    blocks=(heading,),
                    tokenizer=tokenizer,
                    chunker_version=STRUCTURE_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent.chunk_id,
                    section_id=section.section_id,
                    heading_path=heading_path,
                    metadata={
                        "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
                        "semantic_unit_count": 1,
                        "heading_only_section": True,
                        "context_source_block_ids": [],
                        "overlap_source_block_ids": [],
                    },
                )
            )
            ordinal += 1
        pending: list[_SemanticRetrievalUnit] = []
        pending_overlap_count = 0

        def emit_normal(
            pending_units: list[_SemanticRetrievalUnit] = pending,
            section_prefix: str = prefix,
            section_heading: Block | None = heading,
            parent_id: ChunkId = parent.chunk_id,
            section_id: SectionId = section.section_id,
            section_heading_path: tuple[str, ...] = heading_path,
        ) -> tuple[_SemanticRetrievalUnit, ...]:
            nonlocal ordinal, pending_overlap_count
            if not pending_units:
                return ()
            text = _render_normal_units(section_prefix, pending_units)
            token_count = len(tokenizer.encode(text))
            if token_count > config.hard_max_tokens:
                raise ChunkingError("a structure child exceeds structure hard_max_tokens")
            overlap_units = tuple(pending_units[:pending_overlap_count])
            context_blocks = (
                (section_heading,) if section_heading is not None else ()
            )
            chunks.append(
                _chunk(
                    document,
                    text=text,
                    blocks=tuple(
                        block for unit in pending_units for block in unit.blocks
                    ),
                    tokenizer=tokenizer,
                    chunker_version=STRUCTURE_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent_id,
                    section_id=section_id,
                    heading_path=section_heading_path,
                    metadata={
                        "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
                        "semantic_unit_count": len(pending_units),
                        "oversized_text_block_split": any(
                            unit.oversized_split for unit in pending_units
                        ),
                        "rendered_heading_prefix": bool(section_prefix),
                        "context_source_block_ids": [
                            str(block.block_id) for block in context_blocks
                        ],
                        "overlap_source_block_ids": [
                            str(block.block_id)
                            for unit in overlap_units
                            for block in unit.blocks
                        ],
                    },
                    token_count=token_count,
                    extra_provenance_ids=(
                        identifier
                        for block in context_blocks
                        for identifier in block.provenance_ids
                    ),
                )
            )
            ordinal += 1
            tail = _overlap_tail(pending_units, config.semantic_overlap_units)
            pending_units.clear()
            pending_overlap_count = 0
            return tail

        def emit_protected(
            unit: _SemanticRetrievalUnit,
            section_prefix: str = prefix,
            section_heading: Block | None = heading,
            parent_id: ChunkId = parent.chunk_id,
            section_id: SectionId = section.section_id,
            section_heading_path: tuple[str, ...] = heading_path,
        ) -> None:
            nonlocal ordinal
            text = _render_normal_units(section_prefix, (unit,))
            token_count = len(tokenizer.encode(text))
            if token_count > config.hard_max_tokens:
                raise ChunkingError(
                    f"a protected {unit.semantic_type.value} unit exceeds "
                    "structure hard_max_tokens"
                )
            context_blocks = (
                (section_heading,) if section_heading is not None else ()
            )
            chunks.append(
                _chunk(
                    document,
                    text=text,
                    blocks=unit.blocks,
                    tokenizer=tokenizer,
                    chunker_version=STRUCTURE_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent_id,
                    section_id=section_id,
                    heading_path=section_heading_path,
                    metadata={
                        "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
                        "protected_unit": unit.semantic_type.value,
                        "rendered_heading_prefix": bool(section_prefix),
                        "context_source_block_ids": [
                            str(block.block_id) for block in context_blocks
                        ],
                        "overlap_source_block_ids": [],
                    },
                    token_count=token_count,
                    extra_provenance_ids=(
                        identifier
                        for block in context_blocks
                        for identifier in block.provenance_ids
                    ),
                )
            )
            ordinal += 1

        def emit_table(
            unit: _SemanticRetrievalUnit,
            section_prefix: str = prefix,
            parent_id: ChunkId = parent.chunk_id,
            section_id: SectionId = section.section_id,
            section_heading_path: tuple[str, ...] = heading_path,
        ) -> None:
            nonlocal ordinal
            chunks.append(
                _build_table_chunk(
                    document,
                    unit,
                    tokenizer,
                    config,
                    config_hash,
                    ordinal,
                    parent_chunk_id=parent_id,
                    section_id=section_id,
                    heading_path=section_heading_path,
                    rendered_heading_prefix=bool(section_prefix),
                )
            )
            ordinal += 1

        for unit in units:
            if unit.chunk_type is ChunkType.TABLE:
                emit_normal()
                emit_table(unit)
                continue
            if unit.protected_boundary:
                emit_normal()
                emit_protected(unit)
                continue

            candidate = _render_normal_units(prefix, (*pending, unit))
            if pending and len(tokenizer.encode(candidate)) > config.target_tokens:
                overlap = emit_normal()
                overlap_candidate = _render_normal_units(prefix, (*overlap, unit))
                if overlap and len(tokenizer.encode(overlap_candidate)) <= config.target_tokens:
                    pending.extend(overlap)
                    pending_overlap_count = len(overlap)
            single = _render_normal_units(prefix, (unit,))
            if not pending:
                single_tokens = len(tokenizer.encode(single))
                if single_tokens > config.hard_max_tokens:
                    for split in _split_normal_unit(unit, prefix, tokenizer, config):
                        pending.append(split)
                        emit_normal()
                    continue
                if single_tokens > config.target_tokens:
                    pending.append(unit)
                    emit_normal()
                    continue
            pending.append(unit)
        emit_normal()

    for block in evidence.isolated_unresolved_blocks:
        if block.block_id in bound_table_caption_ids:
            continue
        if block.block_type is BlockType.TABLE and block.content_ref is not None:
            if block.content_ref in seen_tables:
                continue
            table = tables_by_id.get(block.content_ref)
            if table is not None:
                seen_tables.add(block.content_ref)
                for unit in _table_units(
                    document, block, table, None, tokenizer, config
                ):
                    chunks.append(
                        _build_table_chunk(
                            document,
                            unit,
                            tokenizer,
                            config,
                            config_hash,
                            ordinal,
                            parent_chunk_id=None,
                            section_id=None,
                            heading_path=(),
                            rendered_heading_prefix=False,
                            reading_order_policy="ISOLATED_UNRESOLVED",
                        )
                    )
                    ordinal += 1
                continue
        unit = _normal_unit(document, block)
        assert unit is not None
        isolated_units: tuple[_SemanticRetrievalUnit, ...] = (unit,)
        if len(tokenizer.encode(unit.text)) > config.hard_max_tokens:
            if unit.protected_boundary:
                raise ChunkingError(
                    f"an unresolved protected {unit.semantic_type.value} unit exceeds "
                    "structure hard_max_tokens"
                )
            isolated_units = _split_normal_unit(unit, "", tokenizer, config)
        for isolated in isolated_units:
            token_count = len(tokenizer.encode(isolated.text))
            chunks.append(
                _chunk(
                    document,
                    text=isolated.text,
                    blocks=isolated.blocks,
                    tokenizer=tokenizer,
                    chunker_version=STRUCTURE_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    metadata={
                        "policy": "RELATIONSHIP_BOUND_SEMANTIC_PACKING_V2",
                        "protected_unit": (
                            isolated.semantic_type.value
                            if isolated.protected_boundary
                            else None
                        ),
                        "oversized_text_block_split": isolated.oversized_split,
                        "reading_order_policy": "ISOLATED_UNRESOLVED",
                        "source_reading_order_status": "UNRESOLVED",
                        "context_source_block_ids": [],
                        "overlap_source_block_ids": [],
                    },
                    token_count=token_count,
                )
            )
            ordinal += 1

    if any(
        chunk.embedding_eligible and chunk.token_count > config.hard_max_tokens
        for chunk in chunks
    ):
        raise ChunkingError("an embedding-eligible structure chunk exceeds the hard limit")
    return tuple(chunks)

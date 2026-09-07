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
from docparser.ir.tables import Table, TableCell
from docparser.ir.types import Sha256Digest

FIXED_CHUNKER_VERSION = "ir-fixed-token@1.0.0"
STRUCTURE_CHUNKER_VERSION = "ir-structure-aware@1.0.0"


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
    hard_max_tokens: int = Field(default=8000, strict=True, ge=1)

    @model_validator(mode="after")
    def _validate_limits(self) -> Self:
        if self.hard_max_tokens < self.target_tokens:
            raise ValueError("structure hard_max_tokens must be >= target_tokens")
        return self


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    blocks: tuple[Block, ...]
    chunk_type: ChunkType = ChunkType.CHILD
    table: Table | None = None
    row_start: int | None = None
    row_end: int | None = None
    repeated_header_rows: tuple[int, ...] = ()


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


def _ordered_blocks(document: DocumentIR) -> tuple[Block, ...]:
    blocks: list[Block] = []
    for page in sorted(document.pages, key=lambda item: item.page_number):
        flow = [
            block
            for block in page.blocks
            if block.reading_order_status is ReadingOrderStatus.IN_FLOW
            and block.block_type in RETRIEVAL_FLOW_BLOCK_TYPES
        ]
        blocks.extend(sorted(flow, key=lambda block: block.reading_order or 0))
    return tuple(blocks)


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


def _unique_provenance(blocks: Iterable[Block], table: Table | None) -> tuple[ProvenanceId, ...]:
    values: list[ProvenanceId] = []
    seen: set[ProvenanceId] = set()
    candidates = [identifier for block in blocks for identifier in block.provenance_ids]
    if table is not None:
        candidates.extend(table.provenance_ids)
        candidates.extend(identifier for cell in table.cells for identifier in cell.provenance_ids)
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
) -> Chunk:
    selected = _unique_blocks(blocks)
    if not selected:
        raise ChunkingError("a chunk must resolve to at least one source block")
    token_count = len(tokenizer.encode(text))
    source_entity_ids = tuple(
        dict.fromkeys(block.content_ref for block in selected if block.content_ref is not None)
    )
    if table is not None and table.table_id not in source_entity_ids:
        source_entity_ids += (table.table_id,)
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
    pages = tuple(block.page_number for block in selected)
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
        bboxes=tuple(
            ChunkBBox(page_number=block.page_number, bbox=block.bbox) for block in selected
        ),
        content_types=tuple(dict.fromkeys(block.block_type for block in selected)),
        token_count=token_count,
        tokenizer_id=tokenizer.tokenizer_id,
        content_digest=content_digest,
        embedding_input_digest=content_digest,
        embedding_eligible=(
            bool(text.strip()) if embedding_eligible is None else embedding_eligible
        ),
        sparse_eligible=bool(text.strip()),
        metadata=metadata or {},
        provenance_ids=_unique_provenance(selected, table),
    )


def fixed_token_chunks(
    document: DocumentIR,
    tokenizer: Tokenizer,
    config: FixedChunkConfig | None = None,
) -> tuple[Chunk, ...]:
    """Create the controlled continuous-token baseline over Canonical IR blocks."""

    config = config or FixedChunkConfig()
    token_ids: list[int] = []
    ranges: list[tuple[int, int, Block]] = []
    for block in _ordered_blocks(document):
        rendered = _render_block(document, block)
        if not rendered.strip():
            continue
        start = len(token_ids)
        token_ids.extend(tokenizer.encode(rendered + "\n\n"))
        ranges.append((start, len(token_ids), block))
    if not token_ids:
        return ()

    config_hash = _config_hash(config)
    all_blocks = tuple(block for _, _, block in ranges)
    parent = _chunk(
        document,
        text=tokenizer.decode(token_ids).rstrip(),
        blocks=all_blocks,
        tokenizer=tokenizer,
        chunker_version=FIXED_CHUNKER_VERSION,
        config_hash=config_hash,
        ordinal=0,
        chunk_type=ChunkType.PARENT,
        metadata={"policy": "FIXED_TOKEN", "context_scope": "DOCUMENT"},
        embedding_eligible=False,
    )
    chunks: list[Chunk] = [parent]
    step = config.target_tokens - config.overlap_tokens
    for ordinal, start in enumerate(range(0, len(token_ids), step), start=1):
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
                parent_chunk_id=parent.chunk_id,
                metadata={
                    "policy": "FIXED_TOKEN",
                    "token_start": start,
                    "token_end": end,
                    "overlap_tokens": config.overlap_tokens,
                },
            )
        )
        if end == len(token_ids):
            break
    return tuple(chunks)


def _heading_prefix(heading: Block | None) -> tuple[str, tuple[str, ...]]:
    if heading is None or not (heading.text or "").strip():
        return "", ()
    value = (heading.text or "").strip()
    return f"# {value}\n\n", (value,)


def _table_source_blocks(
    table: Table, fallback: Block, blocks_by_id: dict[BlockId, Block]
) -> tuple[Block, ...]:
    blocks = tuple(
        blocks_by_id[segment.block_id]
        for segment in table.segments
        if segment.block_id in blocks_by_id
    )
    return blocks or (fallback,)


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


def _table_units(
    document: DocumentIR,
    block: Block,
    table: Table,
    heading: Block | None,
    tokenizer: Tokenizer,
    config: StructureChunkConfig,
) -> tuple[_Unit, ...]:
    blocks_by_id = _block_by_id(document)
    source_blocks = _table_source_blocks(table, block, blocks_by_id)
    prefix, _ = _heading_prefix(heading)
    full_text = prefix + _render_table(table)
    if len(tokenizer.encode(full_text)) <= config.target_tokens:
        return (
            _Unit(
                text=full_text,
                blocks=source_blocks,
                chunk_type=ChunkType.TABLE,
                table=table,
            ),
        )

    header_rows = table.header_row_indices
    data_rows = tuple(row for row in range(table.logical_row_count) if row not in header_rows)
    bands = _row_bands(table, data_rows)
    if not bands:
        bands = (tuple(range(table.logical_row_count)),)
        header_rows = ()
    header_text = _render_table(table, header_rows)
    units: list[_Unit] = []
    current: list[int] = []

    def render(rows: Sequence[int]) -> str:
        parts = [prefix.rstrip(), header_text, _render_table(table, rows)]
        return "\n".join(part for part in parts if part).strip()

    def flush() -> None:
        if not current:
            return
        text = render(current)
        units.append(
            _Unit(
                text=text,
                blocks=source_blocks,
                chunk_type=ChunkType.TABLE,
                table=table,
                row_start=min(current),
                row_end=max(current) + 1,
                repeated_header_rows=header_rows,
            )
        )
        current.clear()

    for band in bands:
        candidate = (*current, *band)
        candidate_text = render(candidate)
        if current and len(tokenizer.encode(candidate_text)) > config.target_tokens:
            flush()
            candidate = band
            candidate_text = render(candidate)
        if len(tokenizer.encode(candidate_text)) > config.hard_max_tokens:
            raise ChunkingError("a complete logical table row exceeds structure hard_max_tokens")
        current.extend(band)
    flush()
    return tuple(units)


def _split_normal_unit(
    unit: _Unit,
    prefix: str,
    tokenizer: Tokenizer,
    config: StructureChunkConfig,
) -> tuple[_Unit, ...]:
    prefix_tokens = tokenizer.encode(prefix)
    capacity = config.target_tokens - len(prefix_tokens)
    if capacity <= 0:
        raise ChunkingError("section heading consumes the complete structure token budget")
    body_tokens = tokenizer.encode(unit.text)
    result: list[_Unit] = []
    for start in range(0, len(body_tokens), capacity):
        text = prefix + tokenizer.decode(body_tokens[start : start + capacity])
        if len(tokenizer.encode(text)) > config.hard_max_tokens:
            raise ChunkingError("an oversized text split exceeds structure hard_max_tokens")
        result.append(_Unit(text=text, blocks=unit.blocks))
    return tuple(result)


def structure_aware_chunks(
    document: DocumentIR,
    tokenizer: Tokenizer,
    config: StructureChunkConfig | None = None,
) -> tuple[Chunk, ...]:
    """Pack section-owned units while preserving logical table rows and heading context."""

    config = config or StructureChunkConfig()
    if not document.sections:
        raise ChunkingError("structure-aware chunking requires materialized sections")
    blocks_by_id = _block_by_id(document)
    tables_by_id = _table_by_id(document)
    config_hash = _config_hash(config)
    chunks: list[Chunk] = []
    seen_tables: set[ContentEntityId] = set()
    ordinal = 0

    for section in document.sections:
        heading = (
            blocks_by_id.get(section.heading_block_id)
            if section.heading_block_id is not None
            else None
        )
        prefix, heading_path = _heading_prefix(heading)
        units: list[_Unit] = []
        parent_parts: list[str] = []
        for block_id in section.content_block_ids:
            block = blocks_by_id[block_id]
            if (
                block.reading_order_status is not ReadingOrderStatus.IN_FLOW
                or block.block_type not in RETRIEVAL_FLOW_BLOCK_TYPES
            ):
                continue
            if block.block_type is BlockType.TABLE and block.content_ref is not None:
                if block.content_ref in seen_tables:
                    continue
                table = tables_by_id.get(block.content_ref)
                if table is not None:
                    seen_tables.add(block.content_ref)
                    parent_parts.append(_render_table(table))
                    units.extend(_table_units(document, block, table, heading, tokenizer, config))
                    continue
            rendered = _render_block(document, block).strip()
            if rendered:
                parent_parts.append(rendered)
                units.append(_Unit(text=rendered, blocks=(block,)))

        parent_blocks = (heading,) if heading is not None else ()
        parent_blocks += tuple(block for unit in units for block in unit.blocks)
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
            metadata={"policy": "STRUCTURE_AWARE", "context_scope": "SECTION"},
            embedding_eligible=False,
        )
        chunks.append(parent)
        ordinal += 1

        pending: list[_Unit] = []

        def emit_pending(
            pending_units: list[_Unit] = pending,
            section_heading: Block | None = heading,
            section_prefix: str = prefix,
            section_heading_path: tuple[str, ...] = heading_path,
            section_id: SectionId = section.section_id,
            parent_id: ChunkId = parent.chunk_id,
        ) -> None:
            nonlocal ordinal
            if not pending_units:
                return
            selected_blocks = (section_heading,) if section_heading is not None else ()
            selected_blocks += tuple(block for unit in pending_units for block in unit.blocks)
            text = section_prefix + "\n\n".join(unit.text for unit in pending_units)
            chunks.append(
                _chunk(
                    document,
                    text=text,
                    blocks=selected_blocks,
                    tokenizer=tokenizer,
                    chunker_version=STRUCTURE_CHUNKER_VERSION,
                    config_hash=config_hash,
                    ordinal=ordinal,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent_id,
                    section_id=section_id,
                    heading_path=section_heading_path,
                    metadata={
                        "policy": "STRUCTURE_AWARE",
                        "rendered_heading_prefix": bool(section_prefix),
                    },
                )
            )
            ordinal += 1
            pending_units.clear()

        for unit in units:
            if unit.chunk_type is ChunkType.TABLE:
                emit_pending()
                table_blocks = (heading,) if heading is not None else ()
                table_blocks += unit.blocks
                metadata: dict[str, JsonValue] = {
                    "policy": "STRUCTURE_AWARE",
                    "protected_unit": "TABLE",
                    "rendered_heading_prefix": bool(prefix),
                }
                if unit.row_start is not None:
                    metadata.update(
                        {
                            "row_start": unit.row_start,
                            "row_end_exclusive": unit.row_end,
                            "repeated_header_rows": list(unit.repeated_header_rows),
                        }
                    )
                chunks.append(
                    _chunk(
                        document,
                        text=unit.text,
                        blocks=table_blocks,
                        tokenizer=tokenizer,
                        chunker_version=STRUCTURE_CHUNKER_VERSION,
                        config_hash=config_hash,
                        ordinal=ordinal,
                        chunk_type=ChunkType.TABLE,
                        parent_chunk_id=parent.chunk_id,
                        section_id=section.section_id,
                        heading_path=heading_path,
                        table=unit.table,
                        metadata=metadata,
                    )
                )
                ordinal += 1
                continue

            candidate = prefix + "\n\n".join(item.text for item in (*pending, unit))
            if pending and len(tokenizer.encode(candidate)) > config.target_tokens:
                emit_pending()
            single = prefix + unit.text
            if not pending and len(tokenizer.encode(single)) > config.target_tokens:
                for split in _split_normal_unit(unit, prefix, tokenizer, config):
                    selected_blocks = (heading,) if heading is not None else ()
                    selected_blocks += split.blocks
                    chunks.append(
                        _chunk(
                            document,
                            text=split.text,
                            blocks=selected_blocks,
                            tokenizer=tokenizer,
                            chunker_version=STRUCTURE_CHUNKER_VERSION,
                            config_hash=config_hash,
                            ordinal=ordinal,
                            chunk_type=ChunkType.CHILD,
                            parent_chunk_id=parent.chunk_id,
                            section_id=section.section_id,
                            heading_path=heading_path,
                            metadata={
                                "policy": "STRUCTURE_AWARE",
                                "oversized_text_block_split": True,
                                "rendered_heading_prefix": bool(prefix),
                            },
                        )
                    )
                    ordinal += 1
            else:
                pending.append(unit)
        emit_pending()
    return tuple(chunks)

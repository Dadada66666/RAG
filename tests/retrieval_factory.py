"""Small synthetic IR and runtime fixtures for offline retrieval tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from docparser.ir.enums import (
    BlockType,
    ConfidenceSource,
    ExtractionMethod,
    ReadingOrderStatus,
    TableCellHeaderRole,
    TextDirection,
)
from docparser.ir.geometry import AffineTransform, BBox, Rotation
from docparser.ir.ids import (
    BlockId,
    ProvenanceId,
    RevisionId,
    TableCellId,
    TableId,
    TableSegmentId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.models import Block, DocumentIR, Page, ProvenanceRecord
from docparser.ir.tables import Table, TableCell, TableSegment
from docparser.ir.types import Sha256Digest, UtcTimestamp
from docparser.normalization.sections import materialize_sections
from tests.ir_factory import ARTIFACT_ID, DOCUMENT_ID, TEST_NAMESPACE, make_document

PAGE_BOX = BBox((0.0, 0.0, 600.0, 800.0))
SECTION_REVISION = RevisionId("rev_018bcfe5-6800-7000-8000-000000000099")


class CharacterTokenizer:
    @property
    def tokenizer_id(self) -> str:
        return "synthetic-character@1"

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(ord(character) for character in text)

    def decode(self, token_ids: Sequence[int]) -> str:
        return "".join(chr(value) for value in token_ids)


class FakeEmbeddingRuntime:
    def __init__(self) -> None:
        self._tokenizer = CharacterTokenizer()
        self.calls: list[tuple[str, ...]] = []

    @property
    def tokenizer(self) -> CharacterTokenizer:
        return self._tokenizer

    @property
    def model_id(self) -> str:
        return "fake-dense@1"

    @property
    def model_digest(self) -> Sha256Digest:
        return Sha256Digest(f"sha256:{'f' * 64}")

    def embed(self, texts: Sequence[str]) -> Any:
        self.calls.append(tuple(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = np.asarray(
                [
                    1.0 + lowered.count("revenue"),
                    1.0 + lowered.count("risk"),
                    1.0 + lowered.count("table"),
                ],
                dtype=np.float32,
            )
            vector /= np.linalg.norm(vector)
            vectors.append(vector.tolist())
        return np.asarray(vectors, dtype=np.float32)


def _id(kind: type[BlockId] | type[ProvenanceId], name: str) -> str:
    return str(generate_uuid5_id(kind, TEST_NAMESPACE, "retrieval", name))


def _provenance(name: str, page_number: int, bbox: BBox) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=ProvenanceId(_id(ProvenanceId, name)),
        document_id=DOCUMENT_ID,
        source_artifact_id=ARTIFACT_ID,
        page_number=page_number,
        bbox=bbox,
        source_coordinate_space="CANONICAL_PAGE_POINTS",
        source_bbox=bbox,
        to_canonical_transform=AffineTransform((1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
        parser_run_id=None,
        source_parser=None,
        parser_version=None,
        extraction_method=ExtractionMethod.IMPORTED,
        original_object_id=name,
        confidence=None,
        char_range=None,
        parent_provenance_ids=(),
        operation="SYNTHETIC_RETRIEVAL_FIXTURE",
    )


def _block(
    name: str,
    block_type: BlockType,
    page_number: int,
    order: int | None,
    status: ReadingOrderStatus,
    *,
    content_ref: TableId | None = None,
) -> tuple[Block, ProvenanceRecord]:
    position = order or 0
    bbox = BBox((30.0, 40.0 + position * 70.0, 570.0, 90.0 + position * 70.0))
    provenance = _provenance(name, page_number, bbox)
    return (
        Block(
            block_id=BlockId(_id(BlockId, name)),
            block_type=block_type,
            page_number=page_number,
            bbox=bbox,
            polygon=None,
            reading_order=order,
            reading_order_status=status,
            text=name,
            text_spans=(),
            text_direction=TextDirection.LTR,
            language="en",
            confidence=None,
            confidence_source=ConfidenceSource.DERIVED,
            parent_block_id=None,
            relationship_ids=(),
            provenance_ids=(provenance.provenance_id,),
            content_ref=content_ref,
            style=None,
            extensions={},
        ),
        provenance,
    )


def make_retrieval_document() -> DocumentIR:
    table_id = generate_uuid5_id(TableId, TEST_NAMESPACE, "retrieval-table")
    page_specs = (
        (
            ("running header", BlockType.HEADER, None, ReadingOrderStatus.DECORATIVE, None),
            ("Revenue", BlockType.HEADING, 0, ReadingOrderStatus.IN_FLOW, None),
            (
                "Revenue increased strongly in 2025.",
                BlockType.PARAGRAPH,
                1,
                ReadingOrderStatus.IN_FLOW,
                None,
            ),
            ("table source", BlockType.TABLE, 2, ReadingOrderStatus.IN_FLOW, table_id),
            (
                "unresolved multicolumn text",
                BlockType.PARAGRAPH,
                None,
                ReadingOrderStatus.UNRESOLVED,
                None,
            ),
        ),
        (
            ("Risk", BlockType.HEADING, 0, ReadingOrderStatus.IN_FLOW, None),
            (
                "Risk factors remain material.",
                BlockType.PARAGRAPH,
                1,
                ReadingOrderStatus.IN_FLOW,
                None,
            ),
            ("future label", BlockType.UNKNOWN, 2, ReadingOrderStatus.IN_FLOW, None),
            ("page footer", BlockType.FOOTER, None, ReadingOrderStatus.DECORATIVE, None),
        ),
    )
    pages: list[Page] = []
    provenance: list[ProvenanceRecord] = []
    table_block: Block | None = None
    for page_number, specs in enumerate(page_specs, start=1):
        page_provenance = _provenance(f"page-{page_number}", page_number, PAGE_BOX)
        provenance.append(page_provenance)
        blocks: list[Block] = []
        for name, block_type, order, status, content_ref in specs:
            block, record = _block(
                name, block_type, page_number, order, status, content_ref=content_ref
            )
            blocks.append(block)
            provenance.append(record)
            if block_type is BlockType.TABLE:
                table_block = block
        pages.append(
            Page(
                page_id=generate_page_id(DOCUMENT_ID, page_number),
                page_number=page_number,
                width=600.0,
                height=800.0,
                rotation_applied=Rotation.DEG_0,
                media_box_original=PAGE_BOX,
                crop_box_original=PAGE_BOX,
                blocks=tuple(blocks),
                page_metadata={},
                provenance_ids=(page_provenance.provenance_id,),
                extensions={},
            )
        )
    assert table_block is not None
    cells: list[TableCell] = []
    values = (("Metric", "Value"), ("Revenue", "120"), ("Profit", "30"), ("Margin", "20"))
    for row, values_in_row in enumerate(values):
        for column, text in enumerate(values_in_row):
            header = row == 0
            cells.append(
                TableCell(
                    cell_id=generate_uuid5_id(
                        TableCellId, TEST_NAMESPACE, "retrieval-cell", str(row), str(column)
                    ),
                    row_index=row,
                    column_index=column,
                    row_span=1,
                    column_span=1,
                    text=text,
                    is_header=header,
                    header_role=(
                        TableCellHeaderRole.COLUMN_HEADER
                        if header
                        else TableCellHeaderRole.NONE
                    ),
                    page_number=1,
                    bbox=None,
                    source_block_ids=(),
                    confidence=None,
                    provenance_ids=table_block.provenance_ids,
                    fragments=(),
                    extensions={},
                )
            )
    table = Table(
        table_id=table_id,
        logical_row_count=4,
        logical_column_count=2,
        segments=(
            TableSegment(
                segment_id=generate_uuid5_id(TableSegmentId, TEST_NAMESPACE, "retrieval-segment"),
                page_number=1,
                bbox=table_block.bbox,
                block_id=table_block.block_id,
                row_start=0,
                row_end_exclusive=4,
                continued_from_segment_id=None,
                continues_to_segment_id=None,
                provenance_ids=table_block.provenance_ids,
                extensions={},
            ),
        ),
        cells=tuple(cells),
        caption_block_ids=(),
        header_row_indices=(0,),
        provenance_ids=table_block.provenance_ids,
        confidence=None,
        extensions={},
    )
    payload = make_document(blocks=()).model_dump(mode="python")
    payload.update(
        {
            "page_count": 2,
            "pages": tuple(pages),
            "tables": (table,),
            "provenance": tuple(provenance),
        }
    )
    document = DocumentIR.model_validate(payload)
    return materialize_sections(
        document,
        revision_id_factory=lambda: SECTION_REVISION,
        clock=lambda: UtcTimestamp("2026-09-07T12:00:00Z"),
    )


def write_model_stub(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"model_type":"bge-m3"}\n', encoding="utf-8")

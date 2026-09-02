"""Document-level referential, topology, and provenance invariants."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from docparser.ir.chunks import Chunk
from docparser.ir.content import Figure, Section
from docparser.ir.enums import BlockType, ChunkType, RelationshipType
from docparser.ir.fingerprints import semantic_fingerprint
from docparser.ir.ids import ProvenanceId, SectionId
from docparser.ir.models import Block, DocumentIR, Page, ProvenanceRecord
from docparser.ir.relationships import Relationship
from docparser.ir.tables import Table, TableSegment

T = TypeVar("T")


def _unique_by_id(  # noqa: UP047 - mypy 1.11 lacks PEP 695 support
    items: Iterable[T], attribute: str, label: str
) -> dict[str, T]:
    indexed: dict[str, T] = {}
    for item in items:
        identifier = str(getattr(item, attribute))
        if identifier in indexed:
            raise ValueError(f"{label} IDs must be unique")
        indexed[identifier] = item
    return indexed


def validate_document_invariants(document: DocumentIR) -> None:
    """Validate cross-entity invariants that require the complete IR graph."""

    pages = {page.page_number: page for page in document.pages}
    blocks = {str(block.block_id): block for page in document.pages for block in page.blocks}
    sections = _unique_by_id(document.sections, "section_id", "section")
    tables = _unique_by_id(document.tables, "table_id", "table")
    figures = _unique_by_id(document.figures, "figure_id", "figure")
    equations = _unique_by_id(document.equations, "equation_id", "equation")
    references = _unique_by_id(document.references, "reference_id", "reference")
    chunks = _unique_by_id(document.chunks, "chunk_id", "chunk")
    relationships = _unique_by_id(document.relationships, "relationship_id", "relationship")

    segments = _unique_by_id(
        (segment for table in document.tables for segment in table.segments),
        "segment_id",
        "table segment",
    )
    cells = _unique_by_id(
        (cell for table in document.tables for cell in table.cells),
        "cell_id",
        "table cell",
    )
    provenance = _validate_provenance_registry(document, pages)

    entity_kinds: dict[str, str] = {str(document.document_id): "DOCUMENT"}
    entity_kinds.update((str(page.page_id), "PAGE") for page in document.pages)
    entity_kinds.update((identifier, "BLOCK") for identifier in blocks)
    entity_kinds.update((identifier, "SECTION") for identifier in sections)
    entity_kinds.update((identifier, "TABLE") for identifier in tables)
    entity_kinds.update((identifier, "TABLE_SEGMENT") for identifier in segments)
    entity_kinds.update((identifier, "TABLE_CELL") for identifier in cells)
    entity_kinds.update((identifier, "FIGURE") for identifier in figures)
    entity_kinds.update((identifier, "EQUATION") for identifier in equations)
    entity_kinds.update((identifier, "REFERENCE") for identifier in references)
    entity_kinds.update((identifier, "CHUNK") for identifier in chunks)
    alternative_cell_pairs = {
        frozenset((str(relationship.source_id), str(relationship.target_id)))
        for relationship in relationships.values()
        if relationship.type is RelationshipType.ALTERNATIVE_TO
        and entity_kinds.get(str(relationship.source_id)) == "TABLE_CELL"
        and entity_kinds.get(str(relationship.target_id)) == "TABLE_CELL"
    }

    _validate_block_graph(document, blocks, relationships)
    _validate_sections(document, sections, blocks, provenance)
    _validate_tables(document, blocks, pages, provenance, alternative_cell_pairs)
    _validate_content(document, blocks, pages, provenance)
    _validate_relationships(
        relationships,
        entity_kinds,
        blocks,
        pages,
        provenance,
        sections,
        tables,
        figures,
        segments,
    )
    _validate_chunks(document, chunks, sections, entity_kinds, blocks, pages, provenance)


def _validate_provenance_registry(
    document: DocumentIR,
    pages: dict[int, Page],
) -> dict[ProvenanceId, ProvenanceRecord]:
    if document.source.source_artifact_id not in document.processing.artifact_ids:
        raise ValueError("source artifact must be listed in processing.artifact_ids")
    artifact_ids = set(document.processing.artifact_ids)
    parser_run_ids = {run.parser_run_id for run in document.processing.parser_runs}
    provenance: dict[ProvenanceId, ProvenanceRecord] = {}
    for record in document.provenance:
        if record.provenance_id in provenance:
            raise ValueError("provenance_id must be unique")
        provenance[record.provenance_id] = record
        if record.document_id != document.document_id:
            raise ValueError("provenance document_id must match DocumentIR.document_id")
        if record.source_artifact_id not in artifact_ids:
            raise ValueError("provenance source_artifact_id is not in processing manifest")
        if record.parser_run_id is not None and record.parser_run_id not in parser_run_ids:
            raise ValueError("provenance parser_run_id is not in processing manifest")
        if record.source_parser is not None:
            if record.parser_run_id is None or record.parser_version is None:
                raise ValueError("parser-derived provenance requires parser run and version")
        if record.page_number is not None:
            page = pages.get(record.page_number)
            if page is None:
                raise ValueError("provenance page_number does not exist")
            if record.bbox is not None and not page.geometry.contains_bbox(record.bbox):
                raise ValueError("provenance bbox lies outside canonical page bounds")
            if record.to_canonical_transform is not None and record.source_bbox is not None:
                for corner in record.source_bbox.corners():
                    if not record.to_canonical_transform.round_trip_within_tolerance(
                        corner,
                        page.geometry,
                    ):
                        raise ValueError("provenance transform exceeds round-trip tolerance")

    for record in document.provenance:
        for parent_id in record.parent_provenance_ids:
            if parent_id not in provenance:
                raise ValueError("parent provenance reference does not resolve")
            if parent_id == record.provenance_id:
                raise ValueError("provenance record cannot be its own parent")
    _validate_acyclic_provenance(provenance)

    for page in document.pages:
        _require_provenance(page.provenance_ids, provenance, page.page_number)
        for block in page.blocks:
            _require_provenance(block.provenance_ids, provenance, page.page_number)
            for span in block.text_spans:
                _require_provenance(span.provenance_ids, provenance, page.page_number)
    return provenance


def _validate_acyclic_provenance(
    provenance: dict[ProvenanceId, ProvenanceRecord],
) -> None:
    visiting: set[ProvenanceId] = set()
    visited: set[ProvenanceId] = set()

    def visit(provenance_id: ProvenanceId) -> None:
        if provenance_id in visiting:
            raise ValueError("provenance lineage contains a cycle")
        if provenance_id in visited:
            return
        visiting.add(provenance_id)
        for parent_id in provenance[provenance_id].parent_provenance_ids:
            visit(parent_id)
        visiting.remove(provenance_id)
        visited.add(provenance_id)

    for provenance_id in provenance:
        visit(provenance_id)


def _require_provenance(
    ids: tuple[ProvenanceId, ...],
    provenance: dict[ProvenanceId, ProvenanceRecord],
    page_number: int | None = None,
) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError("provenance references must be unique per entity")
    for provenance_id in ids:
        record = provenance.get(provenance_id)
        if record is None:
            raise ValueError("entity provenance reference does not resolve")
        if page_number is not None and record.page_number != page_number:
            raise ValueError("entity provenance must resolve to the same page")


def _validate_block_graph(
    document: DocumentIR,
    blocks: dict[str, Block],
    relationships: dict[str, Relationship],
) -> None:
    for block in blocks.values():
        if block.parent_block_id is not None and str(block.parent_block_id) not in blocks:
            raise ValueError("parent_block_id does not resolve")
        if block.semantic_fingerprint is not None:
            if block.semantic_fingerprint != semantic_fingerprint(block):
                raise ValueError("block semantic_fingerprint does not match semantic content")
        linked_ids = {str(block.block_id)}
        if block.content_ref is not None:
            linked_ids.add(str(block.content_ref))
        for relationship_id in block.relationship_ids:
            relationship = relationships.get(str(relationship_id))
            if relationship is None:
                raise ValueError("block relationship_id does not resolve")
            source_linked = str(relationship.source_id) in linked_ids
            target_linked = str(relationship.target_id) in linked_ids
            if not source_linked and not target_linked:
                raise ValueError(
                    "block relationship_id is not connected to the block or its content"
                )

    visited: set[str] = set()
    for block in blocks.values():
        path: set[str] = set()
        current = block
        while str(current.block_id) not in visited:
            current_id = str(current.block_id)
            if current_id in path:
                raise ValueError("parent_block_id graph contains a cycle")
            path.add(current_id)
            if current.parent_block_id is None:
                break
            current = blocks[str(current.parent_block_id)]
        visited.update(path)

    expected: dict[BlockType, set[str]] = {
        BlockType.TABLE: {str(table.table_id) for table in document.tables},
        BlockType.FIGURE: {str(figure.figure_id) for figure in document.figures},
        BlockType.EQUATION: {str(equation.equation_id) for equation in document.equations},
    }
    for block in blocks.values():
        targets = expected.get(block.block_type)
        if targets is not None:
            if block.content_ref is None or str(block.content_ref) not in targets:
                raise ValueError("structured block content_ref does not resolve to its entity type")


def _validate_sections(
    document: DocumentIR,
    sections: dict[str, Section],
    blocks: dict[str, Block],
    provenance: dict[ProvenanceId, ProvenanceRecord],
) -> None:
    for section in sections.values():
        if section.page_end > document.page_count:
            raise ValueError("section page range exceeds document pages")
        _require_provenance(section.provenance_ids, provenance)
        if section.heading_block_id is not None:
            heading = blocks.get(str(section.heading_block_id))
            if heading is None:
                raise ValueError("section heading_block_id does not resolve")
            if heading.block_type not in {BlockType.TITLE, BlockType.HEADING}:
                raise ValueError("section heading_block_id must reference a title or heading")
            if not section.page_start <= heading.page_number <= section.page_end:
                raise ValueError("section heading lies outside section page range")
        for block_id in section.content_block_ids:
            block = blocks.get(str(block_id))
            if block is None:
                raise ValueError("section content_block_id does not resolve")
            if not section.page_start <= block.page_number <= section.page_end:
                raise ValueError("section content block lies outside section page range")

        if section.parent_section_id is not None:
            parent = sections.get(str(section.parent_section_id))
            if parent is None:
                raise ValueError("section parent_section_id does not resolve")
            if section.section_id not in parent.child_section_ids:
                raise ValueError("section parent/child references must be reciprocal")
            if parent.page_start > section.page_start or parent.page_end < section.page_end:
                raise ValueError("child section page range must be nested in parent range")
        for child_id in section.child_section_ids:
            child = sections.get(str(child_id))
            if child is None or child.parent_section_id != section.section_id:
                raise ValueError("section child/parent references must be reciprocal")

    _validate_section_cycles(sections)
    _validate_section_ranges(sections.values())


def _validate_section_cycles(sections: dict[str, Section]) -> None:
    visited: set[str] = set()
    for section in sections.values():
        path: set[str] = set()
        current = section
        while str(current.section_id) not in visited:
            current_id = str(current.section_id)
            if current_id in path:
                raise ValueError("section graph contains a cycle")
            path.add(current_id)
            if current.parent_section_id is None:
                break
            current = sections[str(current.parent_section_id)]
        visited.update(path)


def _validate_section_ranges(sections: Iterable[Section]) -> None:
    ordered = sorted(sections, key=lambda section: (section.page_start, -section.page_end))
    open_ranges: list[Section] = []
    for section in ordered:
        while open_ranges and section.page_start > open_ranges[-1].page_end:
            open_ranges.pop()
        if open_ranges and section.page_end > open_ranges[-1].page_end:
            raise ValueError("section page ranges must be nested or disjoint")
        open_ranges.append(section)


def _validate_tables(
    document: DocumentIR,
    blocks: dict[str, Block],
    pages: dict[int, Page],
    provenance: dict[ProvenanceId, ProvenanceRecord],
    alternative_cell_pairs: set[frozenset[str]],
) -> None:
    for table in document.tables:
        _validate_table_grid(document, table, alternative_cell_pairs)
        _require_provenance(table.provenance_ids, provenance)
        for caption_id in table.caption_block_ids:
            caption = blocks.get(str(caption_id))
            if caption is None or caption.block_type is not BlockType.FIGURE_CAPTION:
                raise ValueError("table caption_block_id must reference a caption block")
        for segment in table.segments:
            page = pages.get(segment.page_number)
            block = blocks.get(str(segment.block_id))
            if page is None or not page.geometry.contains_bbox(segment.bbox):
                raise ValueError("table segment geometry is outside its page")
            if block is None or block.block_type is not BlockType.TABLE:
                raise ValueError("table segment block_id must reference a table block")
            if block.page_number != segment.page_number or block.content_ref != table.table_id:
                raise ValueError("table segment block does not match table or page")
            _require_provenance(segment.provenance_ids, provenance, segment.page_number)
        for cell in table.cells:
            page = pages.get(cell.page_number)
            if page is None:
                raise ValueError("table cell page_number does not exist")
            if cell.bbox is not None and not page.geometry.contains_bbox(cell.bbox):
                raise ValueError("table cell bbox lies outside its page")
            for block_id in cell.source_block_ids:
                if str(block_id) not in blocks:
                    raise ValueError("table cell source_block_id does not resolve")
            _require_provenance(cell.provenance_ids, provenance, cell.page_number)
            for fragment in cell.fragments:
                fragment_page = pages.get(fragment.page_number)
                if fragment_page is None or not fragment_page.geometry.contains_bbox(fragment.bbox):
                    raise ValueError("table cell fragment bbox lies outside its page")
                _require_provenance(fragment.provenance_ids, provenance, fragment.page_number)


def _validate_table_grid(
    document: DocumentIR,
    table: Table,
    alternative_cell_pairs: set[frozenset[str]],
) -> None:
    occupied: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        for row in range(cell.row_index, cell.row_index + cell.row_span):
            for column in range(cell.column_index, cell.column_index + cell.column_span):
                coordinate = (row, column)
                previous_cell_id = occupied.get(coordinate)
                if previous_cell_id is not None:
                    pair = frozenset((previous_cell_id, str(cell.cell_id)))
                    if document.quality_summary.publishable or pair not in alternative_cell_pairs:
                        raise ValueError("table cells overlap in the logical grid")
                occupied[coordinate] = str(cell.cell_id)


def _validate_content(
    document: DocumentIR,
    blocks: dict[str, Block],
    pages: dict[int, Page],
    provenance: dict[ProvenanceId, ProvenanceRecord],
) -> None:
    artifact_ids = set(document.processing.artifact_ids)
    for figure in document.figures:
        _require_provenance(figure.provenance_ids, provenance)
        for block_id in figure.block_ids:
            block = blocks.get(str(block_id))
            if block is None or block.block_type is not BlockType.FIGURE:
                raise ValueError("figure block_id must reference a figure block")
            if block.content_ref != figure.figure_id:
                raise ValueError("figure block content_ref does not match figure_id")
            if block.page_number not in figure.page_numbers:
                raise ValueError("figure block page is absent from figure page_numbers")
        for caption_id in figure.caption_block_ids:
            caption = blocks.get(str(caption_id))
            if caption is None or caption.block_type is not BlockType.FIGURE_CAPTION:
                raise ValueError("figure caption_block_id must reference a caption block")
        if any(artifact_id not in artifact_ids for artifact_id in figure.asset_artifact_ids):
            raise ValueError("figure asset_artifact_id is not in processing manifest")
        if any(page_number not in pages for page_number in figure.page_numbers):
            raise ValueError("figure page_number does not exist")

    for equation in document.equations:
        block = blocks.get(str(equation.block_id))
        if block is None or block.block_type is not BlockType.EQUATION:
            raise ValueError("equation block_id must reference an equation block")
        if block.content_ref != equation.equation_id:
            raise ValueError("equation block content_ref does not match equation_id")
        _require_provenance(equation.provenance_ids, provenance, block.page_number)

    for reference in document.references:
        for block_id in reference.source_block_ids:
            if str(block_id) not in blocks:
                raise ValueError("reference source_block_id does not resolve")
        _require_provenance(reference.provenance_ids, provenance)


_RELATIONSHIP_COMPATIBILITY: dict[RelationshipType, set[tuple[str, str]]] = {
    RelationshipType.CONTAINS: {
        ("DOCUMENT", "SECTION"),
        ("SECTION", "SECTION"),
        ("SECTION", "BLOCK"),
    },
    RelationshipType.CAPTION_OF: {
        ("BLOCK", "FIGURE"),
        ("BLOCK", "TABLE"),
        ("BLOCK", "EQUATION"),
    },
    RelationshipType.CONTINUES_ON: {
        ("BLOCK", "BLOCK"),
        ("TABLE_SEGMENT", "TABLE_SEGMENT"),
    },
    RelationshipType.FOOTNOTE_OF: {
        ("BLOCK", "BLOCK"),
        ("BLOCK", "TABLE"),
        ("BLOCK", "FIGURE"),
        ("BLOCK", "EQUATION"),
    },
    RelationshipType.REFERENCES: {
        ("BLOCK", "BLOCK"),
        ("BLOCK", "TABLE"),
        ("BLOCK", "FIGURE"),
        ("BLOCK", "EQUATION"),
        ("BLOCK", "REFERENCE"),
        ("REFERENCE", "BLOCK"),
        ("CHUNK", "REFERENCE"),
    },
    RelationshipType.READING_NEXT: {("BLOCK", "BLOCK")},
}


def _validate_relationships(
    relationships: dict[str, Relationship],
    entity_kinds: dict[str, str],
    blocks: dict[str, Block],
    pages: dict[int, Page],
    provenance: dict[ProvenanceId, ProvenanceRecord],
    sections: dict[str, Section],
    tables: dict[str, Table],
    figures: dict[str, Figure],
    segments: dict[str, TableSegment],
) -> None:
    for relationship in relationships.values():
        source = str(relationship.source_id)
        target = str(relationship.target_id)
        source_kind = entity_kinds.get(source)
        target_kind = entity_kinds.get(target)
        if source_kind is None or target_kind is None:
            raise ValueError("relationship source or target does not resolve")
        if source == target:
            raise ValueError("relationship source and target must differ")
        _validate_relationship_compatibility(relationship.type, source_kind, target_kind)
        _require_provenance(relationship.provenance_ids, provenance)
        _validate_structural_relationship(
            relationship,
            source_kind,
            target_kind,
            sections,
            tables,
            figures,
            segments,
        )
        if relationship.type is RelationshipType.READING_NEXT:
            _validate_reading_next(blocks[source], blocks[target], pages)
        if relationship.type is RelationshipType.CAPTION_OF:
            if blocks[source].block_type is not BlockType.FIGURE_CAPTION:
                raise ValueError("CAPTION_OF source must be a caption block")
        if relationship.type is RelationshipType.FOOTNOTE_OF:
            if blocks[source].block_type is not BlockType.FOOTNOTE:
                raise ValueError("FOOTNOTE_OF source must be a footnote block")


def _validate_structural_relationship(
    relationship: Relationship,
    source_kind: str,
    target_kind: str,
    sections: dict[str, Section],
    tables: dict[str, Table],
    figures: dict[str, Figure],
    segments: dict[str, TableSegment],
) -> None:
    source = str(relationship.source_id)
    target = str(relationship.target_id)
    if relationship.type is RelationshipType.CONTAINS:
        if source_kind == "DOCUMENT" and sections[target].parent_section_id is not None:
            raise ValueError("document CONTAINS must target a root section")
        if source_kind == "SECTION" and target_kind == "SECTION":
            if sections[target].parent_section_id != sections[source].section_id:
                raise ValueError("section CONTAINS disagrees with section hierarchy")
        if source_kind == "SECTION" and target_kind == "BLOCK":
            if relationship.target_id not in sections[source].content_block_ids:
                raise ValueError("section CONTAINS disagrees with content_block_ids")

    if relationship.type is RelationshipType.CAPTION_OF:
        if target_kind == "FIGURE":
            if relationship.source_id not in figures[target].caption_block_ids:
                raise ValueError("CAPTION_OF disagrees with figure caption_block_ids")
        if target_kind == "TABLE":
            if relationship.source_id not in tables[target].caption_block_ids:
                raise ValueError("CAPTION_OF disagrees with table caption_block_ids")

    if relationship.type is RelationshipType.CONTINUES_ON and source_kind == "TABLE_SEGMENT":
        if segments[source].continues_to_segment_id != relationship.target_id:
            raise ValueError("CONTINUES_ON disagrees with table segment linkage")
        if segments[target].continued_from_segment_id != relationship.source_id:
            raise ValueError("CONTINUES_ON target disagrees with table segment linkage")


def _validate_relationship_compatibility(
    relationship_type: RelationshipType,
    source_kind: str,
    target_kind: str,
) -> None:
    if relationship_type in {RelationshipType.SUPERSEDES, RelationshipType.ALTERNATIVE_TO}:
        if source_kind != target_kind:
            raise ValueError(f"{relationship_type.value} requires matching entity kinds")
        return
    if relationship_type is RelationshipType.DERIVED_FROM:
        if source_kind == "DOCUMENT" or target_kind == "DOCUMENT":
            raise ValueError("DERIVED_FROM does not connect document roots")
        return
    allowed = _RELATIONSHIP_COMPATIBILITY[relationship_type]
    if (source_kind, target_kind) not in allowed:
        raise ValueError(f"{relationship_type.value} is incompatible with entity kinds")


def _validate_reading_next(source: Block, target: Block, pages: dict[int, Page]) -> None:
    if source.reading_order is None or target.reading_order is None:
        raise ValueError("READING_NEXT requires IN_FLOW blocks")
    if source.page_number == target.page_number:
        if target.reading_order != source.reading_order + 1:
            raise ValueError("READING_NEXT disagrees with page reading order")
        return
    if target.page_number != source.page_number + 1 or target.reading_order != 0:
        raise ValueError("cross-page READING_NEXT must connect adjacent page flow boundaries")
    source_page = pages[source.page_number]
    max_order = max(
        block.reading_order for block in source_page.blocks if block.reading_order is not None
    )
    if source.reading_order != max_order:
        raise ValueError("cross-page READING_NEXT source must be the final page flow block")


def _validate_chunks(
    document: DocumentIR,
    chunks: dict[str, Chunk],
    sections: dict[str, Section],
    entity_kinds: dict[str, str],
    blocks: dict[str, Block],
    pages: dict[int, Page],
    provenance: dict[ProvenanceId, ProvenanceRecord],
) -> None:
    heading_paths = _resolve_heading_paths(sections, blocks)
    for chunk in document.chunks:
        if (
            chunk.document_id != document.document_id
            or chunk.ir_revision_id != document.revision_id
        ):
            raise ValueError("chunk document/revision IDs must match DocumentIR")
        source_blocks: list[Block] = []
        for block_id in chunk.source_block_ids:
            block = blocks.get(str(block_id))
            if block is None:
                raise ValueError("chunk source_block_id does not resolve")
            source_blocks.append(block)
        source_pages = [block.page_number for block in source_blocks]
        if chunk.page_start != min(source_pages) or chunk.page_end != max(source_pages):
            raise ValueError("chunk page range must equal resolved source block pages")
        expected_types = tuple(dict.fromkeys(block.block_type for block in source_blocks))
        if chunk.content_types != expected_types:
            raise ValueError("chunk content_types must match ordered source block types")
        for entity_id in chunk.source_entity_ids:
            if str(entity_id) not in entity_kinds:
                raise ValueError("chunk source_entity_id does not resolve")
        for bbox in chunk.bboxes:
            page = pages.get(bbox.page_number)
            if page is None or not page.geometry.contains_bbox(bbox.bbox):
                raise ValueError("chunk bbox lies outside its page")
        if chunk.parent_section_id is None:
            if chunk.heading_path:
                raise ValueError("chunk without parent section must have empty heading_path")
        else:
            if str(chunk.parent_section_id) not in sections:
                raise ValueError("chunk parent_section_id does not resolve")
            if chunk.heading_path != heading_paths[str(chunk.parent_section_id)]:
                raise ValueError("chunk heading_path does not match section hierarchy")
        if chunk.parent_chunk_id is not None:
            parent = chunks.get(str(chunk.parent_chunk_id))
            if parent is None or parent.chunk_type is not ChunkType.PARENT:
                raise ValueError("chunk parent_chunk_id must resolve to a parent chunk")
        elif chunk.chunk_type is ChunkType.CHILD:
            raise ValueError("CHILD chunk requires parent_chunk_id")
        _require_provenance(chunk.provenance_ids, provenance)
    _validate_chunk_cycles(document, chunks)


def _validate_chunk_cycles(document: DocumentIR, chunks: dict[str, Chunk]) -> None:
    visited: set[str] = set()
    for chunk in document.chunks:
        path: set[str] = set()
        current = chunk
        while str(current.chunk_id) not in visited:
            current_id = str(current.chunk_id)
            if current_id in path:
                raise ValueError("chunk parent graph contains a cycle")
            path.add(current_id)
            if current.parent_chunk_id is None:
                break
            current = chunks[str(current.parent_chunk_id)]
        visited.update(path)


def _resolve_heading_paths(
    sections: dict[str, Section],
    blocks: dict[str, Block],
) -> dict[str, tuple[str, ...]]:
    resolved: dict[str, tuple[str, ...]] = {}

    def resolve(section: Section) -> tuple[str, ...]:
        section_id = str(section.section_id)
        if section_id in resolved:
            return resolved[section_id]
        parent_path: tuple[str, ...] = ()
        if section.parent_section_id is not None:
            parent_path = resolve(sections[str(section.parent_section_id)])
        heading: tuple[str, ...] = ()
        if section.heading_block_id is not None:
            text = blocks[str(section.heading_block_id)].text
            if text is not None:
                heading = (text,)
        resolved[section_id] = parent_path + heading
        return resolved[section_id]

    for section in sections.values():
        resolve(section)
    return resolved


def resolve_heading_path(document: DocumentIR, section_id: SectionId) -> tuple[str, ...]:
    """Resolve root-to-leaf section heading text without persisting competing truth."""

    sections = {str(section.section_id): section for section in document.sections}
    blocks = {str(block.block_id): block for page in document.pages for block in page.blocks}
    return _resolve_heading_paths(sections, blocks)[str(section_id)]

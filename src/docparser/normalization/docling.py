"""Normalize parser-neutral evidence into Canonical Document IR."""

from __future__ import annotations

from uuid import UUID

from docparser.domain.parser_contract import (
    CoordinateOrigin,
    ExtractedElementType,
    PageParseResult,
    ParseResult,
    SourceBBox,
)
from docparser.ir.content import Equation, Figure, IssueCounts, QualitySummary
from docparser.ir.enums import (
    BlockType,
    ConfidenceSource,
    Determinism,
    EquationFormat,
    ExtractionMethod,
    QualityStatus,
    ReadingOrderStatus,
    TextDirection,
)
from docparser.ir.geometry import AffineTransform, BBox, Rotation
from docparser.ir.ids import (
    BlockId,
    EquationId,
    FigureId,
    ProvenanceId,
    TableCellId,
    TableId,
    TableSegmentId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.models import (
    Block,
    DocumentIR,
    DocumentMetadata,
    ModelIdentifier,
    Page,
    ParserRunSummary,
    ParserScope,
    ProcessingManifest,
    ProvenanceRecord,
    SourceDocument,
)
from docparser.ir.tables import Table, TableCell, TableSegment
from docparser.normalization.base import NormalizationContext, NormalizationError

NORMALIZER_VERSION = "neutral-normalizer@0.2.0"

_BLOCK_TYPES = {kind.value: BlockType(kind.value) for kind in ExtractedElementType}


def _document_namespace(document_id: str) -> UUID:
    return UUID(document_id.removeprefix("doc_"))


def _bbox_and_transform(
    source: SourceBBox,
    *,
    source_width: float,
    source_height: float,
    canonical_width: float,
    canonical_height: float,
) -> tuple[BBox, AffineTransform]:
    x_scale = canonical_width / source_width
    y_scale = canonical_height / source_height
    if source.origin is CoordinateOrigin.TOP_LEFT:
        transform = AffineTransform((x_scale, 0.0, 0.0, y_scale, 0.0, 0.0))
    else:
        transform = AffineTransform(
            (x_scale, 0.0, 0.0, -y_scale, 0.0, canonical_height)
        )
    points = tuple(transform.apply(point) for point in BBox(
        (source.x0, source.y0, source.x1, source.y1)
    ).corners())
    return (
        BBox((
            min(point.x for point in points),
            min(point.y for point in points),
            max(point.x for point in points),
            max(point.y for point in points),
        )),
        transform,
    )


def _provenance_id(namespace: UUID, *parts: str) -> ProvenanceId:
    return generate_uuid5_id(ProvenanceId, namespace, *parts)


def _block_id(namespace: UUID, parser_name: str, source_object_id: str) -> BlockId:
    return generate_uuid5_id(BlockId, namespace, parser_name, "block", source_object_id)


def _make_page_provenance(
    context: NormalizationContext,
    result: ParseResult,
    page: PageParseResult,
    namespace: UUID,
) -> ProvenanceRecord:
    profile = context.profile.pages[page.page_number - 1]
    page_bbox = BBox((0.0, 0.0, profile.width, profile.height))
    _, transform = _bbox_and_transform(
        SourceBBox(
            x0=0.0,
            y0=0.0,
            x1=page.width,
            y1=page.height,
            origin=CoordinateOrigin.TOP_LEFT,
        ),
        source_width=page.width,
        source_height=page.height,
        canonical_width=profile.width,
        canonical_height=profile.height,
    )
    return ProvenanceRecord(
        provenance_id=_provenance_id(namespace, "page", str(page.page_number)),
        document_id=context.document_id,
        source_artifact_id=context.source_artifact_id,
        page_number=page.page_number,
        bbox=page_bbox,
        source_coordinate_space=(
            f"{result.descriptor.parser_name.upper()}_{page.coordinate_unit.value}_PAGE"
        ),
        source_bbox=BBox((0.0, 0.0, page.width, page.height)),
        to_canonical_transform=transform,
        parser_run_id=result.run.parser_run_id,
        source_parser=result.descriptor.parser_name,
        parser_version=result.descriptor.parser_version,
        extraction_method=ExtractionMethod.IMPORTED,
        original_object_id=f"page:{page.page_number}",
        confidence=None,
        char_range=None,
        parent_provenance_ids=(),
        operation="PAGE_CANONICALIZATION",
    )


def _make_entity_provenance(
    context: NormalizationContext,
    result: ParseResult,
    *,
    namespace: UUID,
    page: PageParseResult,
    source_object_id: str,
    source_bbox: SourceBBox | None,
    confidence: float | None,
    method: str,
    parent_id: ProvenanceId,
) -> ProvenanceRecord:
    profile = context.profile.pages[page.page_number - 1]
    bbox: BBox | None = None
    transform: AffineTransform | None = None
    raw_bbox: BBox | None = None
    if source_bbox is not None:
        bbox, transform = _bbox_and_transform(
            source_bbox,
            source_width=page.width,
            source_height=page.height,
            canonical_width=profile.width,
            canonical_height=profile.height,
        )
        raw_bbox = BBox((source_bbox.x0, source_bbox.y0, source_bbox.x1, source_bbox.y1))
    return ProvenanceRecord(
        provenance_id=_provenance_id(namespace, "entity", source_object_id),
        document_id=context.document_id,
        source_artifact_id=context.source_artifact_id,
        page_number=page.page_number,
        bbox=bbox,
        source_coordinate_space=(
            f"{result.descriptor.parser_name.upper()}_{page.coordinate_unit.value}_"
            f"{source_bbox.origin.value}" if source_bbox is not None else None
        ),
        source_bbox=raw_bbox,
        to_canonical_transform=transform,
        parser_run_id=result.run.parser_run_id,
        source_parser=result.descriptor.parser_name,
        parser_version=result.descriptor.parser_version,
        extraction_method=ExtractionMethod(method),
        original_object_id=source_object_id,
        confidence=confidence,
        char_range=None,
        parent_provenance_ids=(parent_id,),
        operation="NORMALIZE_ENTITY" if source_bbox is not None else "NORMALIZE_PARENT_REGION",
    )


def _content_ids(
    namespace: UUID, pages: tuple[PageParseResult, ...], parser_name: str
) -> tuple[dict[str, TableId], dict[str, FigureId], dict[str, EquationId]]:
    table_ids = {
        table.source_object_id: generate_uuid5_id(
            TableId, namespace, parser_name, "table", table.source_object_id
        )
        for page in pages
        for table in page.tables
    }
    figure_ids = {
        element.source_object_id: generate_uuid5_id(
            FigureId, namespace, parser_name, "figure", element.source_object_id
        )
        for page in pages
        for element in page.elements
        if element.element_type is ExtractedElementType.FIGURE
    }
    equation_ids = {
        element.source_object_id: generate_uuid5_id(
            EquationId, namespace, parser_name, "equation", element.source_object_id
        )
        for page in pages
        for element in page.elements
        if element.element_type is ExtractedElementType.EQUATION
    }
    return table_ids, figure_ids, equation_ids


def _normalize_blocks(
    page: PageParseResult,
    *,
    namespace: UUID,
    provenance_by_source: dict[str, ProvenanceRecord],
    table_ids: dict[str, TableId],
    figure_ids: dict[str, FigureId],
    equation_ids: dict[str, EquationId],
    block_ids: dict[str, BlockId],
) -> tuple[Block, ...]:
    ordered = sorted(
        (
            element
            for element in page.elements
            if element.reading_order_resolved and not element.decorative
        ),
        key=lambda element: (
            element.reading_order if element.reading_order is not None else 0,
            element.source_object_id,
        ),
    )
    canonical_orders = {
        element.source_object_id: index for index, element in enumerate(ordered)
    }
    blocks: list[Block] = []
    for element in page.elements:
        provenance = provenance_by_source[element.source_object_id]
        block_type = _BLOCK_TYPES[element.element_type.value]
        content_ref: TableId | FigureId | EquationId | None = None
        if element.element_type is ExtractedElementType.TABLE:
            content_ref = table_ids.get(element.source_object_id)
            if content_ref is None:
                block_type = BlockType.UNKNOWN
        elif element.element_type is ExtractedElementType.FIGURE:
            content_ref = figure_ids[element.source_object_id]
        elif element.element_type is ExtractedElementType.EQUATION:
            content_ref = equation_ids[element.source_object_id]
        if element.decorative:
            order_status = ReadingOrderStatus.DECORATIVE
            reading_order = None
        elif element.source_object_id in canonical_orders:
            order_status = ReadingOrderStatus.IN_FLOW
            reading_order = canonical_orders[element.source_object_id]
        else:
            order_status = ReadingOrderStatus.UNRESOLVED
            reading_order = None
        if provenance.bbox is None:
            raise NormalizationError("element provenance requires canonical bbox")
        blocks.append(
            Block(
                block_id=block_ids[element.source_object_id],
                block_type=block_type,
                page_number=page.page_number,
                bbox=provenance.bbox,
                polygon=None,
                reading_order=reading_order,
                reading_order_status=order_status,
                text=element.text,
                text_spans=(),
                text_direction=TextDirection.UNKNOWN,
                language=element.language,
                confidence=element.confidence,
                confidence_source=(
                    ConfidenceSource.PARSER if element.confidence is not None else None
                ),
                parent_block_id=block_ids.get(element.parent_source_object_id or ""),
                relationship_ids=(),
                provenance_ids=(provenance.provenance_id,),
                content_ref=content_ref,
                style=None,
                extensions={},
            )
        )
    return tuple(blocks)


def _normalize_tables(
    pages: tuple[PageParseResult, ...],
    *,
    namespace: UUID,
    table_ids: dict[str, TableId],
    provenance_by_source: dict[str, ProvenanceRecord],
    block_ids: dict[str, BlockId],
    caption_block_ids: dict[str, BlockId],
    parser_name: str,
) -> tuple[Table, ...]:
    result: list[Table] = []
    for page in pages:
        for extracted in page.tables:
            table_id = table_ids[extracted.source_object_id]
            table_provenance = provenance_by_source[extracted.source_object_id]
            if table_provenance.bbox is None:
                raise NormalizationError("table provenance requires canonical bbox")
            segment_id = generate_uuid5_id(
                TableSegmentId, namespace, parser_name, "segment", extracted.source_object_id
            )
            cells: list[TableCell] = []
            for cell in extracted.cells:
                cell_id = generate_uuid5_id(
                    TableCellId, namespace, parser_name, "cell", cell.source_object_id
                )
                cell_provenance = provenance_by_source[cell.source_object_id]
                cell_bbox = None
                if cell.bbox is not None:
                    profile = provenance_by_source[cell.source_object_id]
                    cell_bbox = profile.bbox
                cells.append(
                    TableCell(
                        cell_id=cell_id,
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        row_span=cell.row_span,
                        column_span=cell.column_span,
                        text=cell.text,
                        is_header=cell.is_header,
                        page_number=page.page_number,
                        bbox=cell_bbox,
                        source_block_ids=(),
                        confidence=cell.confidence,
                        provenance_ids=(cell_provenance.provenance_id,),
                        fragments=(),
                        extensions={},
                    )
                )
            result.append(
                Table(
                    table_id=table_id,
                    logical_row_count=extracted.row_count,
                    logical_column_count=extracted.column_count,
                    segments=(
                        TableSegment(
                            segment_id=segment_id,
                            page_number=page.page_number,
                            bbox=table_provenance.bbox,
                            block_id=block_ids[extracted.source_object_id],
                            row_start=0,
                            row_end_exclusive=extracted.row_count,
                            continued_from_segment_id=None,
                            continues_to_segment_id=None,
                            provenance_ids=(table_provenance.provenance_id,),
                            extensions={},
                        ),
                    ),
                    cells=tuple(cells),
                    caption_block_ids=tuple(
                        caption_block_ids[caption]
                        for caption in extracted.caption_source_object_ids
                        if caption in caption_block_ids
                    ),
                    header_row_indices=tuple(
                        sorted({cell.row_index for cell in extracted.cells if cell.is_header})
                    ),
                    provenance_ids=(table_provenance.provenance_id,),
                    confidence=extracted.confidence,
                    extensions={},
                )
            )
    return tuple(result)


def normalize_neutral_result(
    result: ParseResult, context: NormalizationContext
) -> DocumentIR:
    """Build a valid, unpublished Canonical IR from parser-neutral evidence."""

    expected = tuple(range(1, context.profile.page_count + 1))
    actual = tuple(page.page_number for page in result.pages)
    if actual != expected:
        raise NormalizationError(
            f"normalization requires complete ordered pages {expected}; received {actual}"
        )
    namespace = _document_namespace(str(context.document_id))
    parser_name = result.descriptor.parser_name
    table_ids, figure_ids, equation_ids = _content_ids(namespace, result.pages, parser_name)
    block_ids = {
        element.source_object_id: _block_id(namespace, parser_name, element.source_object_id)
        for page in result.pages
        for element in page.elements
    }
    caption_block_ids = {
        element.source_object_id: block_ids[element.source_object_id]
        for page in result.pages
        for element in page.elements
        if element.element_type is ExtractedElementType.FIGURE_CAPTION
    }

    provenance: list[ProvenanceRecord] = []
    page_provenance: dict[int, ProvenanceRecord] = {}
    provenance_by_source: dict[str, ProvenanceRecord] = {}
    for page in result.pages:
        page_record = _make_page_provenance(context, result, page, namespace)
        page_provenance[page.page_number] = page_record
        provenance.append(page_record)
        for element in page.elements:
            record = _make_entity_provenance(
                context,
                result,
                namespace=namespace,
                page=page,
                source_object_id=element.source_object_id,
                source_bbox=element.bbox,
                confidence=element.confidence,
                method=element.extraction_method,
                parent_id=page_record.provenance_id,
            )
            provenance_by_source[element.source_object_id] = record
            provenance.append(record)
        for table in page.tables:
            for cell in table.cells:
                record = _make_entity_provenance(
                    context,
                    result,
                    namespace=namespace,
                    page=page,
                    source_object_id=cell.source_object_id,
                    source_bbox=cell.bbox,
                    confidence=cell.confidence,
                    method="TABLE_MODEL",
                    parent_id=provenance_by_source[table.source_object_id].provenance_id,
                )
                provenance_by_source[cell.source_object_id] = record
                provenance.append(record)

    pages = tuple(
        Page(
            page_id=generate_page_id(context.document_id, page.page_number),
            page_number=page.page_number,
            width=context.profile.pages[page.page_number - 1].width,
            height=context.profile.pages[page.page_number - 1].height,
            rotation_applied=Rotation(
                page.rotation
                or context.profile.pages[page.page_number - 1].rotation
            ),
            media_box_original=context.profile.pages[page.page_number - 1].media_box,
            crop_box_original=context.profile.pages[page.page_number - 1].crop_box,
            blocks=_normalize_blocks(
                page,
                namespace=namespace,
                provenance_by_source=provenance_by_source,
                table_ids=table_ids,
                figure_ids=figure_ids,
                equation_ids=equation_ids,
                block_ids=block_ids,
            ),
            page_metadata={
                "document_type": context.profile.document_type.value,
                "likely_scanned": context.profile.pages[page.page_number - 1].likely_scanned,
            },
            provenance_ids=(page_provenance[page.page_number].provenance_id,),
            extensions={},
        )
        for page in result.pages
    )
    tables = _normalize_tables(
        result.pages,
        namespace=namespace,
        table_ids=table_ids,
        provenance_by_source=provenance_by_source,
        block_ids=block_ids,
        caption_block_ids=caption_block_ids,
        parser_name=parser_name,
    )
    figures = tuple(
        Figure(
            figure_id=figure_ids[element.source_object_id],
            block_ids=(block_ids[element.source_object_id],),
            caption_block_ids=tuple(
                block_ids[caption.source_object_id]
                for candidate_page in result.pages
                for caption in candidate_page.elements
                if caption.caption_for_source_object_id == element.source_object_id
            ),
            page_numbers=(element.page_number,),
            asset_artifact_ids=(),
            provenance_ids=(
                provenance_by_source[element.source_object_id].provenance_id,
            ),
            confidence=element.confidence,
            extensions={},
        )
        for page in result.pages
        for element in page.elements
        if element.element_type is ExtractedElementType.FIGURE
    )
    equations = tuple(
        Equation(
            equation_id=equation_ids[element.source_object_id],
            block_id=block_ids[element.source_object_id],
            text=element.text or "",
            format=EquationFormat.PLAIN,
            label=None,
            provenance_ids=(
                provenance_by_source[element.source_object_id].provenance_id,
            ),
            confidence=element.confidence,
            extensions={},
        )
        for page in result.pages
        for element in page.elements
        if element.element_type is ExtractedElementType.EQUATION
    )
    title = next(
        (
            element.text
            for page in result.pages
            for element in page.elements
            if element.element_type is ExtractedElementType.TITLE and element.text
        ),
        None,
    )
    parser_run = ParserRunSummary(
        parser_run_id=result.run.parser_run_id,
        adapter_id=result.descriptor.adapter_id,
        adapter_version=result.descriptor.adapter_version,
        parser_name=result.descriptor.parser_name,
        parser_version=result.descriptor.parser_version,
        model_ids=tuple(
            ModelIdentifier(
                name=model,
                revision=result.descriptor.parser_version,
                digest=None,
                license_approval_id="UNREVIEWED_DEVELOPMENT",
            )
            for model in result.descriptor.model_identifiers
        ),
        capabilities_used=tuple(capability.value for capability in result.descriptor.capabilities),
        scope=ParserScope(kind="DOCUMENT", page_numbers=expected, bbox=None),
        started_at=result.run.started_at,
        ended_at=result.run.ended_at,
        device_class=result.run.actual_device.value,
        determinism=Determinism(result.run.determinism),
        runtime=result.run.runtime,
    )
    return DocumentIR(
        schema_version="1.1.0",
        document_id=context.document_id,
        revision_id=context.revision_id,
        revision_number=0,
        previous_revision_id=None,
        created_at=context.created_at,
        source=SourceDocument(
            source_artifact_id=context.source_artifact_id,
            sha256=context.source_digest,
            media_type="application/pdf",
            size_bytes=context.source_size_bytes,
            original_filename_safe=context.original_filename_safe,
            ingested_at=context.ingested_at,
            source_uri_redacted=None,
            pdf_version=None,
            encryption_status="ENCRYPTED" if context.profile.encrypted else "NOT_ENCRYPTED",
        ),
        metadata=DocumentMetadata(
            title=title,
            authors=(),
            languages=(),
            created_date=None,
            custom={},
        ),
        processing=ProcessingManifest(
            pipeline_version="phase-2.6@0.1.0",
            normalizer_version=NORMALIZER_VERSION,
            validator_ruleset_version="NOT_RUN",
            merge_version="NOT_RUN",
            chunker_version="NOT_RUN",
            renderer_version=context.profile.heuristic_version,
            config_hash=context.config_digest,
            parser_runs=(parser_run,),
            artifact_ids=(context.source_artifact_id,),
        ),
        page_count=context.profile.page_count,
        pages=pages,
        sections=(),
        tables=tables,
        figures=figures,
        equations=equations,
        references=(),
        chunks=(),
        relationships=(),
        provenance=tuple(provenance),
        quality_summary=QualitySummary(
            quality_report_id=None,
            score=None,
            status=QualityStatus.NOT_EVALUATED,
            issue_counts=IssueCounts(INFO=0, WARNING=0, ERROR=0, CRITICAL=0),
            publishable=False,
        ),
        extensions={},
    )


def normalize_docling_result(
    result: ParseResult, context: NormalizationContext
) -> DocumentIR:
    """Compatibility entrypoint for Docling neutral results."""

    return normalize_neutral_result(result, context)

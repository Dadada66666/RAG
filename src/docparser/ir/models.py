"""Strict Phase 1 Canonical Document IR wire models."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from docparser.ir.base import (
    Confidence,
    NonNegativeInt,
    PageNumber,
    PositiveInt,
    StrictInt,
    StrictIRModel,
)
from docparser.ir.chunks import Chunk
from docparser.ir.content import Equation, Figure, QualitySummary, ReferenceEntry, Section
from docparser.ir.enums import (
    BlockType,
    ConfidenceSource,
    Determinism,
    ExtractionMethod,
    ReadingOrderStatus,
    TextDirection,
)
from docparser.ir.geometry import (
    AffineTransform,
    BBox,
    PageGeometry,
    Point,
    PositiveDimension,
    Rotation,
    polygon_is_simple,
)
from docparser.ir.ids import (
    ArtifactId,
    BlockId,
    DocumentId,
    EquationId,
    FigureId,
    PageId,
    ParserRunId,
    ProvenanceId,
    RelationshipId,
    RevisionId,
    TableId,
    generate_page_id,
)
from docparser.ir.relationships import Relationship
from docparser.ir.tables import Table
from docparser.ir.types import (
    MAX_DOCUMENT_EXTENSION_BYTES,
    BoundedJsonObject,
    Extensions,
    LanguageTag,
    NfcString,
    NonEmptyNfcString,
    Sha256Digest,
    UtcTimestamp,
)


class CharacterRange(RootModel[tuple[NonNegativeInt, PositiveInt]]):
    """Half-open Unicode code-point range."""

    model_config = ConfigDict(strict=True, frozen=True)

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if self.start >= self.end:
            raise ValueError("character range must satisfy start < end")
        return self

    @property
    def start(self) -> int:
        return self.root[0]

    @property
    def end(self) -> int:
        return self.root[1]


class TextStyle(StrictIRModel):
    font_family: NfcString | None = None
    font_size_pt: PositiveDimension | None = None
    bold: bool | None = None
    italic: bool | None = None
    monospace: bool | None = None


class TextSpan(StrictIRModel):
    start: NonNegativeInt
    end: PositiveInt
    bbox: BBox | None
    style: TextStyle | None
    language: LanguageTag | None
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_range(self) -> Self:
        if self.start >= self.end:
            raise ValueError("text span must satisfy start < end")
        return self


class Block(StrictIRModel):
    block_id: BlockId
    block_type: BlockType
    page_number: PageNumber
    bbox: BBox
    polygon: Annotated[tuple[Point, ...], Field(min_length=3, max_length=256)] | None
    reading_order: NonNegativeInt | None
    reading_order_status: ReadingOrderStatus
    text: NfcString | None
    text_spans: tuple[TextSpan, ...] = ()
    text_direction: TextDirection
    language: LanguageTag | None
    confidence: Confidence | None
    confidence_source: ConfidenceSource | None
    parent_block_id: BlockId | None
    relationship_ids: tuple[RelationshipId, ...]
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    content_ref: TableId | FigureId | EquationId | None
    style: TextStyle | None
    semantic_fingerprint: Sha256Digest | None = None
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_block_semantics(self) -> Self:
        if self.reading_order_status is ReadingOrderStatus.IN_FLOW:
            if self.reading_order is None:
                raise ValueError("IN_FLOW block requires reading_order")
        elif self.reading_order is not None:
            raise ValueError("non-flow block must not declare reading_order")

        expected_content_type = {
            BlockType.TABLE: TableId,
            BlockType.FIGURE: FigureId,
            BlockType.EQUATION: EquationId,
        }.get(self.block_type)
        if expected_content_type is None and self.content_ref is not None:
            raise ValueError("content_ref is only valid for table, figure, or equation blocks")
        if expected_content_type is not None and self.content_ref is not None:
            if not isinstance(self.content_ref, expected_content_type):
                raise ValueError("content_ref type does not match block_type")

        if self.polygon is not None and not polygon_is_simple(self.polygon):
            raise ValueError("polygon must be simple and non-self-intersecting")

        if self.text_spans:
            if self.text is None:
                raise ValueError("text spans require block text")
            previous_end = 0
            for span in self.text_spans:
                if span.end > len(self.text):
                    raise ValueError("text span lies outside block text")
                if span.start < previous_end:
                    raise ValueError("text spans must be ordered and non-overlapping")
                previous_end = span.end
        return self


class Page(StrictIRModel):
    page_id: PageId
    page_number: PageNumber
    width: PositiveDimension
    height: PositiveDimension
    rotation_applied: Rotation
    media_box_original: BBox | None
    crop_box_original: BBox | None
    blocks: tuple[Block, ...]
    page_metadata: BoundedJsonObject
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_page(self) -> Self:
        geometry = self.geometry
        block_ids: set[BlockId] = set()
        flow_orders: list[int] = []
        for block in self.blocks:
            if block.block_id in block_ids:
                raise ValueError("block_id must be unique within a page")
            block_ids.add(block.block_id)
            if block.page_number != self.page_number:
                raise ValueError("block.page_number must match its containing page")
            if not geometry.contains_bbox(block.bbox):
                raise ValueError("block bbox lies outside canonical page bounds")
            if block.polygon is not None:
                if any(not geometry.contains_point(point) for point in block.polygon):
                    raise ValueError("block polygon lies outside canonical page bounds")
            for span in block.text_spans:
                if span.bbox is not None and not geometry.contains_bbox(span.bbox):
                    raise ValueError("text span bbox lies outside canonical page bounds")
            if block.reading_order_status is ReadingOrderStatus.IN_FLOW:
                assert block.reading_order is not None
                flow_orders.append(block.reading_order)

        if sorted(flow_orders) != list(range(len(flow_orders))):
            raise ValueError("IN_FLOW reading_order must be unique and contiguous from zero")
        return self

    @property
    def geometry(self) -> PageGeometry:
        return PageGeometry((self.width, self.height, self.rotation_applied))


class SourceDocument(StrictIRModel):
    source_artifact_id: ArtifactId
    sha256: Sha256Digest
    media_type: NonEmptyNfcString
    size_bytes: PositiveInt
    original_filename_safe: NonEmptyNfcString
    ingested_at: UtcTimestamp
    source_uri_redacted: NfcString | None = None
    pdf_version: NfcString | None = None
    encryption_status: NfcString | None = None

    @field_validator("original_filename_safe")
    @classmethod
    def _validate_safe_filename(cls, value: str) -> str:
        if len(value) > 255 or any(character in value for character in ("/", "\\", "\x00")):
            raise ValueError("original_filename_safe must be a bounded basename")
        return value


class DocumentMetadata(StrictIRModel):
    title: NfcString | None
    authors: tuple[NfcString, ...]
    languages: tuple[LanguageTag, ...]
    created_date: NfcString | None
    custom: BoundedJsonObject


class ModelIdentifier(StrictIRModel):
    name: NonEmptyNfcString
    revision: NonEmptyNfcString
    digest: Sha256Digest | None
    license_approval_id: NonEmptyNfcString


class ParserScope(StrictIRModel):
    kind: Literal["DOCUMENT", "PAGE", "REGION", "TABLE", "FIGURE", "BLOCK"]
    page_numbers: Annotated[tuple[PageNumber, ...], Field(min_length=1)]
    bbox: BBox | None

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if len(set(self.page_numbers)) != len(self.page_numbers):
            raise ValueError("scope page_numbers must be unique")
        if tuple(sorted(self.page_numbers)) != self.page_numbers:
            raise ValueError("scope page_numbers must be ordered")
        if self.kind == "REGION" and self.bbox is None:
            raise ValueError("REGION scope requires bbox")
        if self.kind != "REGION" and self.bbox is not None:
            raise ValueError("only REGION scope may declare bbox")
        return self


class ParserRunSummary(StrictIRModel):
    parser_run_id: ParserRunId
    adapter_id: NonEmptyNfcString
    adapter_version: NonEmptyNfcString
    parser_name: NonEmptyNfcString
    parser_version: NonEmptyNfcString
    model_ids: tuple[ModelIdentifier, ...]
    capabilities_used: tuple[NonEmptyNfcString, ...]
    scope: ParserScope
    started_at: UtcTimestamp
    ended_at: UtcTimestamp
    device_class: NonEmptyNfcString
    determinism: Determinism
    runtime: BoundedJsonObject


class ProcessingManifest(StrictIRModel):
    pipeline_version: NonEmptyNfcString
    normalizer_version: NonEmptyNfcString
    validator_ruleset_version: NonEmptyNfcString
    merge_version: NonEmptyNfcString
    chunker_version: NonEmptyNfcString
    renderer_version: NonEmptyNfcString
    config_hash: Sha256Digest
    parser_runs: tuple[ParserRunSummary, ...]
    artifact_ids: Annotated[tuple[ArtifactId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        parser_run_ids = [run.parser_run_id for run in self.parser_runs]
        if len(set(parser_run_ids)) != len(parser_run_ids):
            raise ValueError("parser_run_id must be unique")
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("artifact_ids must be unique")
        return self


class ProvenanceRecord(StrictIRModel):
    provenance_id: ProvenanceId
    document_id: DocumentId
    source_artifact_id: ArtifactId
    page_number: PageNumber | None
    bbox: BBox | None
    source_coordinate_space: NfcString | None
    source_bbox: BBox | None
    to_canonical_transform: AffineTransform | None
    parser_run_id: ParserRunId | None
    source_parser: NfcString | None
    parser_version: NfcString | None
    extraction_method: ExtractionMethod
    original_object_id: NfcString | None
    confidence: Confidence | None
    char_range: CharacterRange | None
    parent_provenance_ids: tuple[ProvenanceId, ...]
    operation: NfcString | None

    @model_validator(mode="after")
    def _validate_spatial_scope(self) -> Self:
        if self.page_number is None and self.bbox is not None:
            raise ValueError("document-level provenance must not declare canonical bbox")
        return self


class DocumentIR(StrictIRModel):
    schema_version: Literal["1.1.0"]
    document_id: DocumentId
    revision_id: RevisionId
    revision_number: NonNegativeInt
    previous_revision_id: RevisionId | None
    created_at: UtcTimestamp
    source: SourceDocument
    metadata: DocumentMetadata
    processing: ProcessingManifest
    page_count: Annotated[StrictInt, Field(ge=1, le=1000)]
    pages: Annotated[tuple[Page, ...], Field(min_length=1, max_length=1000)]
    sections: tuple[Section, ...]
    tables: tuple[Table, ...]
    figures: tuple[Figure, ...]
    equations: tuple[Equation, ...]
    references: tuple[ReferenceEntry, ...]
    chunks: tuple[Chunk, ...]
    relationships: tuple[Relationship, ...]
    provenance: Annotated[tuple[ProvenanceRecord, ...], Field(min_length=1)]
    quality_summary: QualitySummary
    extensions: Extensions = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_document(self) -> Self:
        self._validate_revision_lineage()
        self._validate_pages()
        self._validate_extension_budget()
        from docparser.ir.invariants import validate_document_invariants

        validate_document_invariants(self)
        return self

    def _validate_revision_lineage(self) -> None:
        if self.revision_number == 0 and self.previous_revision_id is not None:
            raise ValueError("revision zero must not declare previous_revision_id")
        if self.revision_number > 0 and self.previous_revision_id is None:
            raise ValueError("non-zero revision requires previous_revision_id")
        if self.previous_revision_id == self.revision_id:
            raise ValueError("revision cannot be its own predecessor")

    def _validate_pages(self) -> None:
        if len(self.pages) != self.page_count:
            raise ValueError("len(pages) must equal page_count")
        actual_numbers = tuple(page.page_number for page in self.pages)
        expected_numbers = tuple(range(1, self.page_count + 1))
        if actual_numbers != expected_numbers:
            raise ValueError("pages must be ordered and numbered exactly 1..page_count")

        page_ids: set[PageId] = set()
        block_ids: set[BlockId] = set()
        for page in self.pages:
            if page.page_id in page_ids:
                raise ValueError("page_id must be unique")
            page_ids.add(page.page_id)
            if page.page_id != generate_page_id(self.document_id, page.page_number):
                raise ValueError("page_id does not match document_id and page_number")
            for block in page.blocks:
                if block.block_id in block_ids:
                    raise ValueError("block_id must be unique within a document revision")
                block_ids.add(block.block_id)

    def _validate_extension_budget(self) -> None:
        extension_maps = [self.extensions]
        extension_maps.extend(page.extensions for page in self.pages)
        extension_maps.extend(block.extensions for page in self.pages for block in page.blocks)
        extension_maps.extend(section.extensions for section in self.sections)
        for table in self.tables:
            extension_maps.append(table.extensions)
            extension_maps.extend(segment.extensions for segment in table.segments)
            extension_maps.extend(cell.extensions for cell in table.cells)
        extension_maps.extend(figure.extensions for figure in self.figures)
        extension_maps.extend(equation.extensions for equation in self.equations)
        extension_maps.extend(reference.extensions for reference in self.references)
        extension_maps.extend(relationship.extensions for relationship in self.relationships)
        total_bytes = sum(
            len(
                json.dumps(
                    extension_map,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            for extension_map in extension_maps
        )
        if total_bytes > MAX_DOCUMENT_EXTENSION_BYTES:
            raise ValueError("document extensions exceed the configured 1 MiB bound")

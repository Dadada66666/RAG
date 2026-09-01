"""Versioned retrieval chunk and logical IR packaging wire entities."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from docparser.ir.base import NonNegativeInt, PageNumber, PositiveInt, StrictIRModel
from docparser.ir.enums import BlockType, ChunkType, IRShardKind
from docparser.ir.geometry import BBox
from docparser.ir.ids import (
    ArtifactId,
    BlockId,
    ChunkId,
    ContentEntityId,
    DocumentId,
    EntityId,
    ProvenanceId,
    RevisionId,
    SectionId,
)
from docparser.ir.types import BoundedJsonObject, NfcString, NonEmptyNfcString, Sha256Digest


class ChunkBBox(StrictIRModel):
    page_number: PageNumber
    bbox: BBox


class Chunk(StrictIRModel):
    chunk_id: ChunkId
    document_id: DocumentId
    ir_revision_id: RevisionId
    chunk_schema_version: Literal["1.0.0"]
    chunker_version: NonEmptyNfcString
    chunk_config_hash: Sha256Digest
    chunk_type: ChunkType
    parent_chunk_id: ChunkId | None
    text: NfcString
    parent_section_id: SectionId | None
    heading_path: tuple[NfcString, ...]
    page_start: PageNumber
    page_end: PageNumber
    source_block_ids: Annotated[tuple[BlockId, ...], Field(min_length=1)]
    source_entity_ids: tuple[ContentEntityId, ...]
    bboxes: Annotated[tuple[ChunkBBox, ...], Field(min_length=1)]
    content_types: Annotated[tuple[BlockType, ...], Field(min_length=1)]
    token_count: NonNegativeInt
    tokenizer_id: NonEmptyNfcString
    content_digest: Sha256Digest
    embedding_input_digest: Sha256Digest
    embedding_eligible: bool
    sparse_eligible: bool
    metadata: BoundedJsonObject
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_page_range(self) -> Self:
        if self.page_start > self.page_end:
            raise ValueError("chunk requires page_start <= page_end")
        if any(
            bbox.page_number < self.page_start or bbox.page_number > self.page_end
            for bbox in self.bboxes
        ):
            raise ValueError("chunk bbox page lies outside chunk page range")
        return self


class IRShardDescriptor(StrictIRModel):
    kind: IRShardKind
    page_start: PageNumber | None
    page_end: PageNumber | None
    artifact_id: ArtifactId
    digest: Sha256Digest
    count: PositiveInt

    @model_validator(mode="after")
    def _validate_page_range(self) -> Self:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("shard page_start and page_end must be both set or both null")
        if self.page_start is not None and self.page_end is not None:
            if self.page_start > self.page_end:
                raise ValueError("shard requires page_start <= page_end")
        return self


class GlobalReferenceEntry(StrictIRModel):
    entity_id: EntityId
    shard_index: NonNegativeInt


class IRPackagingManifest(StrictIRModel):
    packaging_version: Literal["1.0.0"]
    schema_version: Literal["1.0.0"]
    document_id: DocumentId
    revision_id: RevisionId
    semantic_digest: Sha256Digest
    shards: Annotated[tuple[IRShardDescriptor, ...], Field(min_length=1)]
    global_reference_index: tuple[GlobalReferenceEntry, ...]

    @model_validator(mode="after")
    def _validate_reference_index(self) -> Self:
        entity_ids = [entry.entity_id for entry in self.global_reference_index]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("global reference entity IDs must be unique")
        if any(entry.shard_index >= len(self.shards) for entry in self.global_reference_index):
            raise ValueError("global reference shard_index does not resolve")
        return self

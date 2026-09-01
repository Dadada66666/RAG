"""Document-wide typed relationship edges."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from docparser.ir.base import Confidence, StrictIRModel
from docparser.ir.enums import RelationshipType
from docparser.ir.ids import EntityId, ProvenanceId, RelationshipId
from docparser.ir.types import BoundedJsonObject, Extensions


class Relationship(StrictIRModel):
    relationship_id: RelationshipId
    type: RelationshipType
    source_id: EntityId
    target_id: EntityId
    confidence: Confidence | None
    provenance_ids: Annotated[tuple[ProvenanceId, ...], Field(min_length=1)]
    metadata: BoundedJsonObject
    extensions: Extensions = Field(default_factory=dict)

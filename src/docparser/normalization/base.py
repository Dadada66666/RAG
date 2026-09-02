"""Normalizer inputs independent of any parser SDK."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from docparser.ir.ids import ArtifactId, DocumentId, RevisionId
from docparser.ir.types import Sha256Digest, UtcTimestamp
from docparser.preflight import DocumentProfile


class NormalizationError(ValueError):
    """Neutral parser output cannot form a valid Canonical IR revision."""


@dataclass(frozen=True, slots=True)
class NormalizationContext:
    namespace: UUID
    tenant_scope: str
    document_id: DocumentId
    revision_id: RevisionId
    source_artifact_id: ArtifactId
    source_digest: Sha256Digest
    source_size_bytes: int
    original_filename_safe: str
    ingested_at: UtcTimestamp
    created_at: UtcTimestamp
    config_digest: Sha256Digest
    profile: DocumentProfile

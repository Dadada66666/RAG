"""Opaque Canonical IR identifiers and centralized generation."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Self, TypeVar
from uuid import UUID, uuid5

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from docparser.ir.types import Sha256Digest

_UUID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class OpaqueId(str):
    """Base for lowercase type-prefixed UUID identifiers."""

    prefix: ClassVar[str]
    allowed_versions: ClassVar[frozenset[int]] = frozenset({5, 7})

    def __new__(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError(f"{cls.__name__} must be a string")
        expected_prefix = f"{cls.prefix}_"
        if not value.startswith(expected_prefix):
            raise ValueError(f"{cls.__name__} must start with {expected_prefix!r}")
        payload = value[len(expected_prefix) :]
        try:
            parsed = UUID(payload)
        except ValueError as exc:
            raise ValueError(f"{cls.__name__} must contain a canonical UUID") from exc
        if str(parsed) != payload:
            raise ValueError(f"{cls.__name__} must use lowercase canonical UUID text")
        if parsed.version not in cls.allowed_versions:
            versions = ", ".join(str(version) for version in sorted(cls.allowed_versions))
            raise ValueError(f"{cls.__name__} requires UUID version {versions}")
        return str.__new__(cls, value)

    @classmethod
    def from_uuid(cls, value: UUID) -> Self:
        return cls(f"{cls.prefix}_{value}")

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(strict=True, pattern=rf"^{cls.prefix}_{_UUID_PATTERN}$"),
            serialization=core_schema.to_string_ser_schema(),
        )


class DocumentId(OpaqueId):
    prefix = "doc"
    allowed_versions = frozenset({5})


class RevisionId(OpaqueId):
    prefix = "rev"
    allowed_versions = frozenset({7})


class PageId(OpaqueId):
    prefix = "page"
    allowed_versions = frozenset({5})


class BlockId(OpaqueId):
    prefix = "blk"
    allowed_versions = frozenset({5})


class ProvenanceId(OpaqueId):
    prefix = "prov"
    allowed_versions = frozenset({5})


class ArtifactId(OpaqueId):
    prefix = "art"
    allowed_versions = frozenset({7})


class ParserRunId(OpaqueId):
    prefix = "prun"
    allowed_versions = frozenset({7})


class RelationshipId(OpaqueId):
    prefix = "rel"
    allowed_versions = frozenset({5})


class TableId(OpaqueId):
    prefix = "tbl"
    allowed_versions = frozenset({5})


class FigureId(OpaqueId):
    prefix = "fig"
    allowed_versions = frozenset({5})


class EquationId(OpaqueId):
    prefix = "eq"
    allowed_versions = frozenset({5})


Uuid5Id = TypeVar("Uuid5Id", bound=OpaqueId)


def _encode_name(parts: Sequence[str]) -> str:
    return "".join(f"{len(part)}:{part}" for part in parts)


def generate_uuid5_id(  # noqa: UP047 - mypy 1.11 lacks PEP 695 generic support
    id_type: type[Uuid5Id],
    namespace: UUID,
    *components: str,
) -> Uuid5Id:
    """Generate a deterministic typed ID without ambiguous component separators."""

    if 5 not in id_type.allowed_versions:
        raise ValueError(f"{id_type.__name__} does not permit UUIDv5")
    if not components or any(component == "" for component in components):
        raise ValueError("UUIDv5 name components must be non-empty")
    return id_type.from_uuid(uuid5(namespace, _encode_name(components)))


def generate_document_id(
    namespace: UUID,
    tenant_scope: str,
    source_digest: Sha256Digest,
) -> DocumentId:
    """Generate the stable document identity for exact bytes within a tenant scope."""

    return generate_uuid5_id(DocumentId, namespace, tenant_scope, source_digest)


def generate_page_id(document_id: DocumentId, page_number: int) -> PageId:
    """Generate a page identity stable across revisions of the same source."""

    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be an integer >= 1")
    document_namespace = UUID(document_id.removeprefix("doc_"))
    return generate_uuid5_id(PageId, document_namespace, "page", str(page_number))


def build_uuid7(timestamp_ms: int, random_bits: int) -> UUID:
    """Build an RFC 9562 UUIDv7 from injected time and 74 bits of entropy."""

    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise TypeError("timestamp_ms must be an integer")
    if not 0 <= timestamp_ms < 1 << 48:
        raise ValueError("timestamp_ms must fit in 48 bits")
    if isinstance(random_bits, bool) or not isinstance(random_bits, int):
        raise TypeError("random_bits must be an integer")
    if not 0 <= random_bits < 1 << 74:
        raise ValueError("random_bits must fit in 74 bits")

    rand_a = random_bits >> 62
    rand_b = random_bits & ((1 << 62) - 1)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return UUID(int=value)


def _system_unix_time_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class RevisionIdGenerator:
    """Injectable UUIDv7 revision ID provider."""

    clock_ms: Callable[[], int] = _system_unix_time_ms
    entropy: Callable[[int], int] = secrets.randbits

    def new(self) -> RevisionId:
        return RevisionId.from_uuid(build_uuid7(self.clock_ms(), self.entropy(74)))

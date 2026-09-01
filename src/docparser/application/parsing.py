"""Development-quality PDF -> Canonical IR vertical slice."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from pydantic import Field

from docparser.domain.parser_contract import (
    ParseRequest,
    ParseResult,
    ParseScope,
    ParseScopeKind,
    RuntimeDevice,
)
from docparser.ir.base import StrictIRModel
from docparser.ir.enums import ReadingOrderStatus
from docparser.ir.ids import (
    ArtifactId,
    DocumentId,
    RevisionId,
    RevisionIdGenerator,
    generate_artifact_id,
    generate_document_id,
)
from docparser.ir.models import DocumentIR
from docparser.ir.serialization import dump_canonical_json
from docparser.ir.types import BoundedJsonObject, Sha256Digest, UtcTimestamp
from docparser.normalization import NormalizationContext, normalize_docling_result
from docparser.ports.parsers import DocumentParser
from docparser.preflight import DocumentProfile, inspect_pdf

DEFAULT_NAMESPACE = UUID("b7e12dde-6b21-5c70-91df-f0af646dde4a")


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


class ParsingConfig(StrictIRModel):
    parser: str = "docling"
    device: RuntimeDevice = RuntimeDevice.AUTO
    tenant_scope: str = "local"
    namespace: UUID = DEFAULT_NAMESPACE


class ParseDiagnostics(StrictIRModel):
    pages_requested: int = Field(strict=True, ge=0)
    pages_parsed: int = Field(strict=True, ge=0)
    pages_missing: tuple[int, ...]
    blocks_by_type: BoundedJsonObject
    tables_detected: int = Field(strict=True, ge=0)
    cells_detected: int = Field(strict=True, ge=0)
    unresolved_reading_order_pages: tuple[int, ...]
    null_confidence_count: int = Field(strict=True, ge=0)
    parser_warnings: tuple[str, ...]
    normalization_warnings: tuple[str, ...]
    ir_validation_passed: bool
    elapsed_seconds: float = Field(strict=True, ge=0.0)
    device: RuntimeDevice
    provenance_complete_blocks: int = Field(strict=True, ge=0)
    generated_blocks: int = Field(strict=True, ge=0)


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    document: DocumentIR
    parse_result: ParseResult
    diagnostics: ParseDiagnostics
    profile: DocumentProfile


def _source_digest(path: Path) -> Sha256Digest:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return Sha256Digest(f"sha256:{digest.hexdigest()}")


def _config_digest(config: ParsingConfig, result: ParseResult) -> Sha256Digest:
    payload = {
        "adapter_version": result.descriptor.adapter_version,
        "device": config.device.value,
        "parser": result.descriptor.parser_name,
        "parser_version": result.descriptor.parser_version,
        "profile": result.descriptor.profile,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def _diagnostics(
    document: DocumentIR,
    result: ParseResult,
    *,
    elapsed_seconds: float,
) -> ParseDiagnostics:
    blocks = [block for page in document.pages for block in page.blocks]
    counts = Counter(block.block_type.value for block in blocks)
    requested = set(result.pages_requested)
    parsed = {page.page_number for page in result.pages}
    unresolved = tuple(
        page.page_number
        for page in document.pages
        if any(
            block.reading_order_status is ReadingOrderStatus.UNRESOLVED
            for block in page.blocks
        )
    )
    provenance_ids = {record.provenance_id for record in document.provenance}
    provenance_complete = sum(
        bool(block.provenance_ids)
        and all(identifier in provenance_ids for identifier in block.provenance_ids)
        for block in blocks
    )
    independent_table_pages = {
        table.segments[0].page_number
        for table in document.tables
        if len(table.segments) == 1
    }
    normalization_warnings = (
        (
            "adjacent pages contain independent table candidates; "
            "cross-page continuity was not inferred"
        ),
    ) if any(
        page_number + 1 in independent_table_pages
        for page_number in independent_table_pages
    ) else ()
    return ParseDiagnostics(
        pages_requested=len(requested),
        pages_parsed=len(parsed),
        pages_missing=tuple(sorted(requested - parsed)),
        blocks_by_type=dict(sorted(counts.items())),
        tables_detected=len(document.tables),
        cells_detected=sum(len(table.cells) for table in document.tables),
        unresolved_reading_order_pages=unresolved,
        null_confidence_count=sum(block.confidence is None for block in blocks),
        parser_warnings=result.warnings
        + tuple(warning for page in result.pages for warning in page.warnings),
        normalization_warnings=normalization_warnings,
        ir_validation_passed=True,
        elapsed_seconds=round(elapsed_seconds, 6),
        device=result.run.actual_device,
        provenance_complete_blocks=provenance_complete,
        generated_blocks=len(blocks),
    )


def parse_document_with_diagnostics(
    path: Path,
    config: ParsingConfig,
    *,
    parser: DocumentParser | None = None,
    raw_output_dir: Path | None = None,
    profile_provider: Callable[[Path], DocumentProfile] = inspect_pdf,
    revision_id_factory: Callable[[], RevisionId] | None = None,
    artifact_id_factory: Callable[[], ArtifactId] = generate_artifact_id,
    clock: Callable[[], UtcTimestamp] = _utc_now,
) -> ParseOutcome:
    """Run preflight, parser, neutral normalization, and IR invariant validation."""

    if config.parser != "docling":
        raise ValueError("Phase 2.5 supports only the docling primary parser")
    if parser is None:
        from docparser.adapters.parsers.docling import DoclingOptions, DoclingParserAdapter

        parser = DoclingParserAdapter(DoclingOptions(device=config.device))
    started = perf_counter()
    profile = profile_provider(path)
    digest = _source_digest(path)
    document_id: DocumentId = generate_document_id(
        config.namespace, config.tenant_scope, digest
    )
    result = parser.parse(
        ParseRequest(
            source_path=path,
            scope=ParseScope(
                kind=ParseScopeKind.PAGE,
                page_numbers=tuple(range(1, profile.page_count + 1)),
            ),
            device=config.device,
            raw_output_dir=raw_output_dir,
        )
    )
    now = clock()
    revision_factory = revision_id_factory or RevisionIdGenerator().new
    context = NormalizationContext(
        namespace=config.namespace,
        tenant_scope=config.tenant_scope,
        document_id=document_id,
        revision_id=revision_factory(),
        source_artifact_id=artifact_id_factory(),
        source_digest=digest,
        source_size_bytes=path.stat().st_size,
        original_filename_safe=path.name,
        ingested_at=now,
        created_at=now,
        config_digest=_config_digest(config, result),
        profile=profile,
    )
    document = normalize_docling_result(result, context)
    diagnostics = _diagnostics(
        document, result, elapsed_seconds=perf_counter() - started
    )
    return ParseOutcome(
        document=document,
        parse_result=result,
        diagnostics=diagnostics,
        profile=profile,
    )


def parse_document(
    path: Path,
    config: ParsingConfig,
    *,
    parser: DocumentParser | None = None,
) -> DocumentIR:
    """Benchmark-friendly application API that does not scrape CLI output."""

    return parse_document_with_diagnostics(path, config, parser=parser).document


def write_parse_outputs(outcome: ParseOutcome, output: Path) -> None:
    """Write the bounded Phase 2.5 development artifacts."""

    output.mkdir(parents=True, exist_ok=True)
    (output / "raw").mkdir(parents=True, exist_ok=True)
    (output / "document.ir.json").write_bytes(dump_canonical_json(outcome.document))
    for name, model in (
        ("parse-result.json", outcome.parse_result),
        ("diagnostics.json", outcome.diagnostics),
    ):
        payload = json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        (output / name).write_text(payload, encoding="utf-8")

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
from typing import Literal
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
from docparser.normalization import (
    NormalizationContext,
    normalize_neutral_result,
)
from docparser.ports.parsers import DocumentParser
from docparser.preflight import DocumentProfile, extract_numeric_tokens, inspect_pdf

DEFAULT_NAMESPACE = UUID("b7e12dde-6b21-5c70-91df-f0af646dde4a")


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


class ParsingConfig(StrictIRModel):
    parser: str = "docling-standard"
    device: RuntimeDevice = RuntimeDevice.AUTO
    tenant_scope: str = "local"
    namespace: UUID = DEFAULT_NAMESPACE


class NumericDisagreement(StrictIRModel):
    code: Literal["NUMERIC_TEXT_DISAGREEMENT"] = "NUMERIC_TEXT_DISAGREEMENT"
    page_number: int = Field(strict=True, ge=1)
    missing_native_values: tuple[str, ...]
    extra_parser_values: tuple[str, ...]


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
    native_text_pages: int = Field(strict=True, ge=0)
    image_only_pages: int = Field(strict=True, ge=0)
    blocks_by_extraction_method: BoundedJsonObject
    table_cells_with_exact_bbox: int = Field(strict=True, ge=0)
    table_cells_without_bbox: int = Field(strict=True, ge=0)
    unresolved_hierarchy_count: int = Field(strict=True, ge=0)
    eligible_retrieval_blocks: int = Field(strict=True, ge=0)
    section_assigned_blocks: int = Field(strict=True, ge=0)
    section_assignment_coverage: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    reading_order_eligible_pages: int = Field(strict=True, ge=0)
    reading_order_resolved_pages: int = Field(strict=True, ge=0)
    resolved_reading_order_page_rate: float | None = Field(
        default=None, strict=True, ge=0.0, le=1.0
    )
    cross_page_table_candidates: int = Field(strict=True, ge=0)
    native_numeric_tokens: int = Field(strict=True, ge=0)
    parser_numeric_tokens: int = Field(strict=True, ge=0)
    numeric_exact_overlaps: int = Field(strict=True, ge=0)
    missing_native_numbers: int = Field(strict=True, ge=0)
    extra_parser_numbers: int = Field(strict=True, ge=0)
    conflicting_numeric_strings: int = Field(strict=True, ge=0)
    numeric_disagreement_count: int = Field(strict=True, ge=0)
    numeric_disagreements: tuple[NumericDisagreement, ...]
    coordinate_validation_warnings: tuple[str, ...]


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
    profile: DocumentProfile,
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
    cell_provenance = {
        record.provenance_id: record
        for record in document.provenance
    }
    extraction_counts = Counter(
        cell_provenance[block.provenance_ids[0]].extraction_method.value
        for block in blocks
    )
    cells = [cell for table in document.tables for cell in table.cells]
    exact_cells = sum(
        any(cell_provenance[identifier].bbox is not None for identifier in cell.provenance_ids)
        for cell in cells
    )
    native_by_page = {
        page.page_number: Counter(
            token.normalized for token in page.native_text_evidence.normalized_numeric_tokens
        )
        for page in profile.pages
    }
    parser_by_page: dict[int, Counter[str]] = {}
    for page in document.pages:
        text_parts = [
            block.text or ""
            for block in page.blocks
            if block.block_type.value != "TABLE"
        ]
        text_parts.extend(
            cell.text
            for table in document.tables
            for cell in table.cells
            if cell.page_number == page.page_number
        )
        parser_by_page[page.page_number] = Counter(
            token.normalized for token in extract_numeric_tokens("\n".join(text_parts))
        )
    exact_overlap = 0
    missing = 0
    extra = 0
    conflicts = 0
    disagreement_records: list[NumericDisagreement] = []
    for page_number, native in native_by_page.items():
        parser_values = parser_by_page.get(page_number, Counter())
        overlap = native & parser_values
        missing_values = native - parser_values
        extra_values = parser_values - native
        exact_overlap += sum(overlap.values())
        missing += sum(missing_values.values())
        extra += sum(extra_values.values())
        native_prefixes = {value.rsplit(".", 1)[0] for value in missing_values}
        parser_prefixes = {value.rsplit(".", 1)[0] for value in extra_values}
        conflicts += len(native_prefixes & parser_prefixes)
        if missing_values or extra_values:
            disagreement_records.append(
                NumericDisagreement(
                    page_number=page_number,
                    missing_native_values=tuple(sorted(missing_values.elements())),
                    extra_parser_values=tuple(sorted(extra_values.elements())),
                )
            )
    coordinate_warnings = tuple(
        f"page {page.page_number}: parser/page aspect ratio differs from canonical CropBox"
        for page in result.pages
        if abs(
            (page.width / page.height)
            - (
                profile.pages[page.page_number - 1].width
                / profile.pages[page.page_number - 1].height
            )
        ) > 0.02
    )
    unresolved_hierarchy = sum(
        element.parent_source_object_id is not None
        and element.parent_source_object_id
        not in {
            candidate.source_object_id
            for candidate_page in result.pages
            for candidate in candidate_page.elements
        }
        for page in result.pages
        for element in page.elements
    )
    retrieval_types = {
        "TITLE",
        "HEADING",
        "PARAGRAPH",
        "LIST",
        "LIST_ITEM",
        "TABLE",
        "FIGURE",
        "FIGURE_CAPTION",
        "EQUATION",
        "CODE",
        "QUOTE",
        "FOOTNOTE",
    }
    eligible_blocks = [
        block for block in blocks if block.block_type.value in retrieval_types
    ]
    section_block_ids = {
        block_id
        for section in document.sections
        for block_id in (
            *((section.heading_block_id,) if section.heading_block_id is not None else ()),
            *section.content_block_ids,
        )
    }
    section_assigned = sum(
        block.block_id in section_block_ids for block in eligible_blocks
    )
    order_eligible_pages = [
        page
        for page in document.pages
        if any(block.block_type.value in retrieval_types for block in page.blocks)
    ]
    order_resolved_pages = sum(
        all(
            block.reading_order_status is ReadingOrderStatus.IN_FLOW
            for block in page.blocks
            if block.block_type.value in retrieval_types
        )
        for page in order_eligible_pages
    )
    explicit_continuations = sum(
        table.continuation_from_source_object_id is not None
        or table.continuation_to_source_object_id is not None
        for page in result.pages
        for table in page.tables
    )
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
        native_text_pages=sum(page.has_text_layer for page in profile.pages),
        image_only_pages=sum(page.likely_image_only for page in profile.pages),
        blocks_by_extraction_method=dict(sorted(extraction_counts.items())),
        table_cells_with_exact_bbox=exact_cells,
        table_cells_without_bbox=len(cells) - exact_cells,
        unresolved_hierarchy_count=unresolved_hierarchy,
        eligible_retrieval_blocks=len(eligible_blocks),
        section_assigned_blocks=section_assigned,
        section_assignment_coverage=(
            section_assigned / len(eligible_blocks) if eligible_blocks else None
        ),
        reading_order_eligible_pages=len(order_eligible_pages),
        reading_order_resolved_pages=order_resolved_pages,
        resolved_reading_order_page_rate=(
            order_resolved_pages / len(order_eligible_pages)
            if order_eligible_pages
            else None
        ),
        cross_page_table_candidates=explicit_continuations,
        native_numeric_tokens=sum(sum(values.values()) for values in native_by_page.values()),
        parser_numeric_tokens=sum(sum(values.values()) for values in parser_by_page.values()),
        numeric_exact_overlaps=exact_overlap,
        missing_native_numbers=missing,
        extra_parser_numbers=extra,
        conflicting_numeric_strings=conflicts,
        numeric_disagreement_count=len(disagreement_records),
        numeric_disagreements=tuple(disagreement_records),
        coordinate_validation_warnings=coordinate_warnings,
    )


def _build_parser(config: ParsingConfig) -> DocumentParser:
    def docling() -> DocumentParser:
        from docparser.adapters.parsers.docling import DoclingOptions, DoclingParserAdapter

        return DoclingParserAdapter(DoclingOptions(device=config.device))

    def paddle() -> DocumentParser:
        from docparser.adapters.parsers.paddleocr_vl import (
            PaddleOCRVLOptions,
            PaddleOCRVLParserAdapter,
        )

        return PaddleOCRVLParserAdapter(PaddleOCRVLOptions(device=config.device))

    builders: dict[str, Callable[[], DocumentParser]] = {
        "docling": docling,
        "docling-standard": docling,
        "paddleocr-vl": paddle,
        "paddleocr-vl-1.6": paddle,
    }
    try:
        return builders[config.parser]()
    except KeyError as exc:
        raise ValueError(f"unknown parser profile: {config.parser}") from exc


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

    if parser is None:
        parser = _build_parser(config)
    started = perf_counter()
    profile = profile_provider(path)
    digest = _source_digest(path)
    document_id: DocumentId = generate_document_id(
        config.namespace, config.tenant_scope, digest
    )
    result = parser.parse(
        ParseRequest(
            source_path=path,
            scope=ParseScope(kind=ParseScopeKind.DOCUMENT),
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
    document = normalize_neutral_result(result, context)
    diagnostics = _diagnostics(
        document, result, profile, elapsed_seconds=perf_counter() - started
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
        ("preflight.json", outcome.profile),
    ):
        payload = json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        (output / name).write_text(payload, encoding="utf-8")

"""Docling 2.123.0 adapter returning only the neutral ParseResult contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docparser.adapters.parsers.docling.mapping import JsonObject, map_docling_document
from docparser.adapters.parsers.docling.options import (
    ADAPTER_VERSION,
    DOCLING_VERSION,
    PROFILE_NAME,
    DoclingOptions,
)
from docparser.adapters.parsers.docling.runtime import (
    DoclingRuntimeError,
    docling_is_compatible,
    installed_docling_version,
    resolve_device,
)
from docparser.domain.parser_contract import (
    ParserCapability,
    ParserDescriptor,
    ParseRequest,
    ParseResult,
    ParserHealth,
    ParserHealthStatus,
    ParserRun,
    ParseScopeKind,
    ParseStatus,
    RuntimeDevice,
)
from docparser.ir.ids import ParserRunId, generate_parser_run_id
from docparser.ir.types import UtcTimestamp


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


class DoclingParserAdapter:
    """Optional primary parser; importing this class does not import Docling."""

    def __init__(
        self,
        options: DoclingOptions | None = None,
        *,
        run_id_factory: Callable[[], ParserRunId] = generate_parser_run_id,
        clock: Callable[[], UtcTimestamp] = _utc_now,
    ) -> None:
        self._options = options or DoclingOptions()
        self._run_id_factory = run_id_factory
        self._clock = clock

    def descriptor(self) -> ParserDescriptor:
        return ParserDescriptor(
            parser_name="docling",
            parser_version=DOCLING_VERSION,
            adapter_id="org.docparser.adapter.docling",
            adapter_version=ADAPTER_VERSION,
            profile=PROFILE_NAME,
            capabilities=(
                ParserCapability.OCR,
                ParserCapability.TABLE,
                ParserCapability.FORMULA,
                ParserCapability.FIGURE,
                ParserCapability.LAYOUT,
                ParserCapability.READING_ORDER,
            ),
            supported_scopes=(ParseScopeKind.DOCUMENT,),
            model_identifiers=(
                "docling-layout-default@2.123.0",
                "docling-tableformer@accurate",
                "rapidocr-ppocrv4@ch",
                "docling-code-formula-default@2.123.0",
            ),
        )

    def health(self) -> ParserHealth:
        installed = installed_docling_version()
        if not docling_is_compatible():
            detail = (
                "Docling optional dependency is not installed"
                if installed is None
                else f"Docling {installed} installed; exact {DOCLING_VERSION} required"
            )
            return ParserHealth(
                status=ParserHealthStatus.UNAVAILABLE,
                requested_device=self._options.device,
                actual_device=None,
                detail=detail,
            )
        try:
            actual = resolve_device(self._options.device)
        except DoclingRuntimeError as exc:
            return ParserHealth(
                status=ParserHealthStatus.UNAVAILABLE,
                requested_device=self._options.device,
                actual_device=None,
                detail=str(exc),
            )
        return ParserHealth(
            status=ParserHealthStatus.READY,
            requested_device=self._options.device,
            actual_device=actual,
            detail=None,
        )

    def parse(self, request: ParseRequest) -> ParseResult:
        if request.scope.kind is not ParseScopeKind.DOCUMENT:
            raise DoclingRuntimeError(
                "docling-standard currently executes complete documents only",
                code="UNSUPPORTED_DOCUMENT",
            )
        if request.source_path.suffix.lower() != ".pdf":
            raise DoclingRuntimeError(
                "Docling vertical slice accepts PDF input only",
                code="UNSUPPORTED_DOCUMENT",
            )
        if not request.source_path.is_file():
            raise DoclingRuntimeError("input PDF does not exist", code="UNSUPPORTED_DOCUMENT")
        if not docling_is_compatible():
            raise DoclingRuntimeError(
                f"install the pinned optional dependency with .[docling] ({DOCLING_VERSION})",
                code="PARSER_UNAVAILABLE",
                retryable=True,
            )

        requested_device = (
            request.device if request.device is not RuntimeDevice.AUTO else self._options.device
        )
        actual_device = resolve_device(requested_device)
        started_at = self._clock()
        run_id = self._run_id_factory()
        try:
            conversion = self._convert(request.source_path, actual_device)
            payload = self._document_payload(conversion)
            self._write_raw_snapshot(payload, request.raw_output_dir)
        except DoclingRuntimeError:
            raise
        except Exception as exc:
            raise DoclingRuntimeError("Docling parser failed") from exc
        ended_at = self._clock()
        pages_requested = self._page_numbers(payload)
        run = ParserRun(
            parser_run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            requested_device=requested_device,
            actual_device=actual_device,
            determinism="BEST_EFFORT",
            runtime={
                "org.docparser.profile": PROFILE_NAME,
                "org.docparser.ocr_engine": self._options.ocr_engine,
                "org.docparser.ocr_languages": list(self._options.ocr_languages),
                "org.docparser.table_mode": self._options.table_mode,
                "org.docparser.page_batch_size": self._options.page_batch_size,
            },
        )
        return map_docling_document(
            payload,
            descriptor=self.descriptor(),
            run=run,
            pages_requested=pages_requested,
            status=self._conversion_status(conversion),
        )

    def _convert(self, source: Path, device: RuntimeDevice) -> Any:
        # All optional SDK imports remain behind this adapter boundary.
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            OcrMode,
            PdfPipelineOptions,
            RapidOcrOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline = PdfPipelineOptions(
            do_ocr=self._options.ocr_enabled,
            ocr_options=RapidOcrOptions(
                lang=list(self._options.ocr_languages),
                backend="torch",
                mode=OcrMode.DEFAULT,
            ),
            do_table_structure=True,
            table_structure_options=TableStructureOptions(
                do_cell_matching=self._options.table_cell_matching,
                mode=TableFormerMode.ACCURATE,
            ),
            do_formula_enrichment=self._options.formula_enrichment,
            do_code_enrichment=self._options.code_enrichment,
            do_picture_description=False,
            do_picture_classification=False,
            enable_remote_services=False,
            allow_external_plugins=False,
            generate_page_images=False,
            generate_picture_images=False,
            generate_table_images=False,
            generate_parsed_pages=False,
            accelerator_options=AcceleratorOptions(
                num_threads=self._options.cpu_threads,
                device=device.value,
            ),
            ocr_batch_size=self._options.page_batch_size,
            layout_batch_size=self._options.page_batch_size,
            table_batch_size=self._options.page_batch_size,
        )
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)},
        )
        return converter.convert(source, raises_on_error=True)

    @staticmethod
    def _document_payload(conversion: Any) -> JsonObject:
        document = getattr(conversion, "document", None)
        if document is None:
            raise DoclingRuntimeError(
                "Docling conversion did not return a document", code="INVALID_OUTPUT"
            )
        payload = document.export_to_dict()
        if not isinstance(payload, dict):
            raise DoclingRuntimeError(
                "Docling document did not serialize as an object", code="INVALID_OUTPUT"
            )
        return payload

    @staticmethod
    def _page_numbers(payload: JsonObject) -> tuple[int, ...]:
        pages = payload.get("pages")
        if not isinstance(pages, dict):
            raise DoclingRuntimeError(
                "Docling output has no page registry", code="INVALID_OUTPUT"
            )
        try:
            return tuple(sorted(int(page) for page in pages))
        except (TypeError, ValueError) as exc:
            raise DoclingRuntimeError(
                "Docling page registry contains invalid keys", code="INVALID_OUTPUT"
            ) from exc

    @staticmethod
    def _conversion_status(conversion: Any) -> ParseStatus:
        value = str(getattr(conversion, "status", "success")).lower()
        return ParseStatus.PARTIAL if "partial" in value else ParseStatus.SUCCESS

    @staticmethod
    def _write_raw_snapshot(payload: JsonObject, output_dir: Path | None) -> None:
        if output_dir is None:
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        item_fields = {
            "self_ref",
            "parent",
            "children",
            "label",
            "prov",
            "text",
            "content_layer",
            "captions",
            "references",
            "data",
        }
        body = payload.get("body")
        sanitized: JsonObject = {
            "schema_name": payload.get("schema_name"),
            "version": payload.get("version"),
            "body": (
                {key: value for key, value in body.items() if key in item_fields}
                if isinstance(body, dict)
                else {}
            ),
            "groups": [
                {key: value for key, value in item.items() if key in item_fields}
                for item in payload.get("groups", [])
                if isinstance(item, dict)
            ],
            "texts": [
                {key: value for key, value in item.items() if key in item_fields}
                for item in payload.get("texts", [])
                if isinstance(item, dict)
            ],
            "pictures": [
                {key: value for key, value in item.items() if key in item_fields}
                for item in payload.get("pictures", [])
                if isinstance(item, dict)
            ],
            "tables": [
                {key: value for key, value in item.items() if key in item_fields}
                for item in payload.get("tables", [])
                if isinstance(item, dict)
            ],
            "pages": {
                str(page_number): {
                    key: value
                    for key, value in page.items()
                    if key in {"size", "rotation"}
                }
                for page_number, page in payload.get("pages", {}).items()
                if isinstance(page, dict)
            },
        }
        snapshot = json.dumps(
            sanitized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = snapshot.encode("utf-8")
        if len(encoded) > 64 * 1024 * 1024:
            snapshot = json.dumps(
                {
                    "content_omitted": True,
                    "page_count": len(sanitized["pages"]),
                    "sanitized_sha256": hashlib.sha256(encoded).hexdigest(),
                    "sanitized_size_bytes": len(encoded),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        (output_dir / "docling-document.json").write_text(snapshot, encoding="utf-8")

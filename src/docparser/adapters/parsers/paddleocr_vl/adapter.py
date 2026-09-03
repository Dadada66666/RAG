"""Full PaddleOCR-VL-1.6 pipeline adapter returning neutral records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docparser.adapters.parsers.paddleocr_vl.mapping import JsonObject, map_paddleocr_vl_pages
from docparser.adapters.parsers.paddleocr_vl.options import (
    ADAPTER_VERSION,
    PADDLEOCR_VERSION,
    PADDLEPADDLE_VERSION,
    PADDLEX_VERSION,
    PROFILE_NAME,
    PaddleOCRVLOptions,
)
from docparser.adapters.parsers.paddleocr_vl.runtime import (
    PaddleOCRVLRuntimeError,
    installed_versions,
    resolve_device,
    runtime_is_compatible,
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
    RuntimeDevice,
)
from docparser.ir.ids import ParserRunId, generate_parser_run_id
from docparser.ir.types import UtcTimestamp


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC).isoformat().replace("+00:00", "Z"))


class PaddleOCRVLParserAdapter:
    def __init__(
        self,
        options: PaddleOCRVLOptions | None = None,
        *,
        run_id_factory: Callable[[], ParserRunId] = generate_parser_run_id,
        clock: Callable[[], UtcTimestamp] = _utc_now,
    ) -> None:
        self._options = options or PaddleOCRVLOptions()
        self._run_id_factory = run_id_factory
        self._clock = clock

    def descriptor(self) -> ParserDescriptor:
        return ParserDescriptor(
            parser_name="paddleocr-vl",
            parser_version=PADDLEOCR_VERSION,
            adapter_id="org.docparser.adapter.paddleocr-vl",
            adapter_version=ADAPTER_VERSION,
            profile=PROFILE_NAME,
            capabilities=tuple(ParserCapability),
            supported_scopes=(ParseScopeKind.DOCUMENT,),
            model_identifiers=(
                "PP-DocLayoutV3@paddlex-3.7.1",
                "PaddleOCR-VL-1.6-0.9B@paddleocr-3.7.0",
            ),
        )

    def health(self) -> ParserHealth:
        if not runtime_is_compatible():
            return ParserHealth(
                status=ParserHealthStatus.UNAVAILABLE,
                requested_device=self._options.device,
                actual_device=None,
                detail=f"pinned Paddle runtime required; installed={installed_versions()}",
            )
        try:
            actual = resolve_device(self._options.device)
        except PaddleOCRVLRuntimeError as exc:
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
            raise PaddleOCRVLRuntimeError(
                "paddleocr-vl-1.6 currently executes complete documents only",
                code="UNSUPPORTED_DOCUMENT",
            )
        if request.source_path.suffix.lower() != ".pdf" or not request.source_path.is_file():
            raise PaddleOCRVLRuntimeError(
                "PaddleOCR-VL adapter requires an existing PDF",
                code="UNSUPPORTED_DOCUMENT",
            )
        if not runtime_is_compatible():
            raise PaddleOCRVLRuntimeError(
                "install .[paddleocr-vl] and PaddlePaddle 3.3.0 for the selected device",
                code="PARSER_UNAVAILABLE",
                retryable=True,
            )
        requested_device = (
            request.device if request.device is not RuntimeDevice.AUTO else self._options.device
        )
        actual_device = resolve_device(requested_device)
        started_at = self._clock()
        try:
            payloads = self._convert(request.source_path, actual_device)
            self._write_raw_snapshot(payloads, request.raw_output_dir)
        except PaddleOCRVLRuntimeError:
            raise
        except Exception as exc:
            raise PaddleOCRVLRuntimeError("PaddleOCR-VL parser failed") from exc
        ended_at = self._clock()
        run = ParserRun(
            parser_run_id=self._run_id_factory(),
            started_at=started_at,
            ended_at=ended_at,
            requested_device=requested_device,
            actual_device=actual_device,
            determinism="BEST_EFFORT",
            runtime={
                "org.docparser.profile": PROFILE_NAME,
                "org.docparser.pipeline_version": self._options.pipeline_version,
                "org.docparser.layout_model": self._options.layout_model,
                "org.docparser.recognition_model": self._options.recognition_model,
                "org.docparser.recognition_backend": self._options.recognition_backend,
                "org.docparser.paddlex_version": PADDLEX_VERSION,
                "org.docparser.paddlepaddle_version": PADDLEPADDLE_VERSION,
            },
        )
        return map_paddleocr_vl_pages(payloads, descriptor=self.descriptor(), run=run)

    def _convert(self, source: Path, device: RuntimeDevice) -> list[JsonObject]:
        from paddleocr import PaddleOCRVL

        pipeline = PaddleOCRVL(
            pipeline_version=self._options.pipeline_version,
            layout_detection_model_name=self._options.layout_model,
            vl_rec_model_name=self._options.recognition_model,
            vl_rec_backend=self._options.recognition_backend,
            use_doc_orientation_classify=self._options.use_doc_orientation_classify,
            use_doc_unwarping=self._options.use_doc_unwarping,
            use_layout_detection=self._options.use_layout_detection,
            merge_layout_blocks=self._options.merge_layout_blocks,
            format_block_content=self._options.format_block_content,
            use_queues=self._options.use_queues,
            device="gpu:0" if device is RuntimeDevice.CUDA else "cpu",
        )
        return [
            self._sanitize_result(result, index)
            for index, result in enumerate(pipeline.predict(input=str(source)))
        ]

    @classmethod
    def _sanitize_result(cls, value: Any, fallback_index: int) -> JsonObject:
        serialized = value.json
        if not isinstance(serialized, Mapping):
            raise PaddleOCRVLRuntimeError(
                "Paddle result.json is not a structured mapping", code="INVALID_OUTPUT"
            )
        if "res" not in serialized:
            raise PaddleOCRVLRuntimeError(
                "Paddle result.json omitted 'res'", code="INVALID_OUTPUT"
            )
        raw = serialized["res"]
        if not isinstance(raw, Mapping):
            raise PaddleOCRVLRuntimeError(
                "Paddle result.json['res'] is not a structured mapping", code="INVALID_OUTPUT"
            )
        payload = cls._json_safe(raw)
        if not isinstance(payload, dict):
            raise PaddleOCRVLRuntimeError(
                "Paddle result could not be sanitized", code="INVALID_OUTPUT"
            )
        payload.pop("input_path", None)
        page_index = payload.get("page_index", fallback_index)
        if not isinstance(page_index, int):
            raise PaddleOCRVLRuntimeError(
                "Paddle output has an invalid page index", code="INVALID_OUTPUT"
            )
        payload["page_index"] = page_index
        width = raw.get("width")
        height = raw.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise PaddleOCRVLRuntimeError(
                "Paddle output omitted source image dimensions", code="INVALID_OUTPUT"
            )
        payload["source_width"] = width
        payload["source_height"] = height
        return payload

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            if not all(isinstance(key, str) for key in value):
                raise PaddleOCRVLRuntimeError(
                    "Paddle result.json contains a non-string key", code="INVALID_OUTPUT"
                )
            return {
                key: cls._json_safe(item)
                for key, item in value.items()
                if key not in {"input_img", "output_img", "input_path"}
            }
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        raise PaddleOCRVLRuntimeError(
            "Paddle result.json contains a non-JSON-safe value", code="INVALID_OUTPUT"
        )

    @staticmethod
    def _write_raw_snapshot(payloads: list[JsonObject], output_dir: Path | None) -> None:
        if output_dir is None:
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {"pages": payloads},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 64 * 1024 * 1024:
            encoded = json.dumps(
                {
                    "content_omitted": True,
                    "page_count": len(payloads),
                    "sanitized_sha256": hashlib.sha256(encoded).hexdigest(),
                    "sanitized_size_bytes": len(encoded),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        (output_dir / "paddleocr-vl-pages.json").write_bytes(encoded)

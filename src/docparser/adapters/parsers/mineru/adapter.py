"""MinerU 3.4.5 Hybrid High adapter using an isolated subprocess runtime."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from docparser.adapters.parsers.mineru.mapping import JsonObject, map_mineru_middle
from docparser.adapters.parsers.mineru.options import (
    ADAPTER_VERSION,
    MINERU_VERSION,
    PROFILE_NAME,
    MinerUOptions,
)
from docparser.adapters.parsers.mineru.runtime import (
    CompletedProcessRunner,
    MinerURuntimeError,
    installed_mineru_version,
    resolve_device,
    runtime_environment,
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


class MinerUParserAdapter:
    """Execute the pinned MinerU profile without importing its Python package."""

    def __init__(
        self,
        options: MinerUOptions | None = None,
        *,
        run_id_factory: Callable[[], ParserRunId] = generate_parser_run_id,
        clock: Callable[[], UtcTimestamp] = _utc_now,
        runner: CompletedProcessRunner = subprocess.run,
    ) -> None:
        self._options = options or MinerUOptions()
        self._run_id_factory = run_id_factory
        self._clock = clock
        self._runner = runner

    def descriptor(self) -> ParserDescriptor:
        return ParserDescriptor(
            parser_name="mineru",
            parser_version=MINERU_VERSION,
            adapter_id="org.docparser.adapter.mineru",
            adapter_version=ADAPTER_VERSION,
            profile=PROFILE_NAME,
            capabilities=tuple(ParserCapability),
            supported_scopes=(ParseScopeKind.DOCUMENT,),
            model_identifiers=("MinerU-3.4.5-hybrid-high-auto@local",),
        )

    def health(self) -> ParserHealth:
        installed = installed_mineru_version(
            self._options.executable,
            runner=self._runner,
        )
        if installed != MINERU_VERSION:
            detail = (
                "MinerU executable is unavailable"
                if installed is None
                else f"MinerU {installed} detected; exact {MINERU_VERSION} required"
            )
            return ParserHealth(
                status=ParserHealthStatus.UNAVAILABLE,
                requested_device=self._options.device,
                actual_device=None,
                detail=detail,
            )
        try:
            actual = resolve_device(self._options.device)
        except MinerURuntimeError as exc:
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
            raise MinerURuntimeError(
                "mineru-hybrid-high executes complete documents only",
                code="UNSUPPORTED_DOCUMENT",
            )
        if request.source_path.suffix.lower() != ".pdf" or not request.source_path.is_file():
            raise MinerURuntimeError(
                "MinerU adapter requires an existing PDF",
                code="UNSUPPORTED_DOCUMENT",
            )
        installed = installed_mineru_version(
            self._options.executable,
            runner=self._runner,
        )
        if installed != MINERU_VERSION:
            raise MinerURuntimeError(
                f"exact MinerU {MINERU_VERSION} executable required",
                code="PARSER_UNAVAILABLE",
                retryable=True,
            )
        requested_device = (
            request.device if request.device is not RuntimeDevice.AUTO else self._options.device
        )
        actual_device = resolve_device(requested_device)
        started_at = self._clock()
        run_id = self._run_id_factory()
        if request.raw_output_dir is None:
            with TemporaryDirectory(prefix="docparser-mineru-") as temporary:
                payload = self._execute(request.source_path, Path(temporary))
        else:
            native_output = request.raw_output_dir / "mineru-native"
            native_output.mkdir(parents=True, exist_ok=True)
            payload = self._execute(request.source_path, native_output)
        ended_at = self._clock()
        run = ParserRun(
            parser_run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            requested_device=requested_device,
            actual_device=actual_device,
            determinism="BEST_EFFORT",
            runtime={
                "org.docparser.profile": PROFILE_NAME,
                "org.mineru.backend": self._options.backend,
                "org.mineru.effort": self._options.effort,
                "org.mineru.parse_method": self._options.parse_method,
                "org.mineru.model_source": self._options.model_source,
            },
        )
        try:
            return map_mineru_middle(payload, descriptor=self.descriptor(), run=run)
        except (KeyError, TypeError, ValueError) as exc:
            raise MinerURuntimeError(
                "MinerU middle.json violates the pinned 3.4.5 Hybrid High contract: "
                f"{exc}",
                code="INVALID_OUTPUT",
            ) from exc

    def _execute(self, source: Path, output: Path) -> JsonObject:
        argv = [
            self._options.executable,
            "-p",
            str(source),
            "-o",
            str(output),
            "-b",
            self._options.backend,
            "--effort",
            self._options.effort,
            "-m",
            self._options.parse_method,
        ]
        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                env=runtime_environment(self._options.model_source),
            )
        except FileNotFoundError as exc:
            raise MinerURuntimeError(
                "MinerU executable is unavailable",
                code="PARSER_UNAVAILABLE",
                retryable=True,
            ) from exc
        if completed.returncode != 0:
            raise MinerURuntimeError(
                f"MinerU subprocess failed with exit code {completed.returncode}",
                code="PARSER_FAILURE",
            )
        middle_files = tuple(output.rglob("*_middle.json"))
        if len(middle_files) != 1:
            raise MinerURuntimeError(
                "MinerU output must contain exactly one *_middle.json",
                code="INVALID_OUTPUT",
            )
        try:
            payload = json.loads(middle_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MinerURuntimeError(
                "MinerU middle.json is not valid JSON",
                code="INVALID_OUTPUT",
            ) from exc
        if not isinstance(payload, dict):
            raise MinerURuntimeError(
                "MinerU middle.json root must be an object",
                code="INVALID_OUTPUT",
            )
        return payload

"""Runtime boundary for the isolated MinerU executable."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Protocol

from docparser.domain.parser_contract import ParserExecutionError, RuntimeDevice

MinerURuntimeError = ParserExecutionError


class CompletedProcessRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


def runtime_environment(model_source: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["MINERU_MODEL_SOURCE"] = model_source
    return environment


def installed_mineru_version(
    executable: str,
    *,
    runner: CompletedProcessRunner = subprocess.run,
) -> str | None:
    try:
        completed = runner(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=runtime_environment("local"),
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    return "3.4.5" if re.search(r"(?<![0-9.])3\.4\.5(?![0-9.])", output) else output or None


def resolve_device(requested: RuntimeDevice) -> RuntimeDevice:
    if requested is RuntimeDevice.CPU:
        raise MinerURuntimeError(
            "mineru-hybrid-high requires its isolated vLLM CUDA runtime",
            code="RUNTIME_UNAVAILABLE",
        )
    return RuntimeDevice.CUDA

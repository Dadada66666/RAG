"""Runtime discovery kept inside the optional Docling adapter boundary."""

from __future__ import annotations

import importlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version

from docparser.adapters.parsers.docling.options import DOCLING_VERSION
from docparser.domain.parser_contract import ParserExecutionError, RuntimeDevice

DoclingRuntimeError = ParserExecutionError


def installed_docling_version() -> str | None:
    try:
        return version("docling")
    except PackageNotFoundError:
        return None


def docling_is_compatible() -> bool:
    return (
        importlib.util.find_spec("docling") is not None
        and installed_docling_version() == DOCLING_VERSION
    )


def cuda_is_available() -> bool:
    if importlib.util.find_spec("torch") is None:
        return False
    torch = importlib.import_module("torch")
    cuda = getattr(torch, "cuda", None)
    is_available = getattr(cuda, "is_available", None)
    return bool(callable(is_available) and is_available())


def resolve_device(requested: RuntimeDevice) -> RuntimeDevice:
    if requested is RuntimeDevice.CPU:
        return RuntimeDevice.CPU
    if requested is RuntimeDevice.CUDA:
        if not cuda_is_available():
            raise DoclingRuntimeError(
                "CUDA was explicitly requested but is unavailable",
                code="RUNTIME_UNAVAILABLE",
                retryable=True,
            )
        return RuntimeDevice.CUDA
    return RuntimeDevice.CUDA if cuda_is_available() else RuntimeDevice.CPU

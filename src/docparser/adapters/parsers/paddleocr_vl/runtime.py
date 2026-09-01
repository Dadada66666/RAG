"""Runtime discovery contained inside the optional Paddle adapter."""

from __future__ import annotations

import importlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version

from docparser.adapters.parsers.paddleocr_vl.options import (
    PADDLEOCR_VERSION,
    PADDLEPADDLE_VERSION,
    PADDLEX_VERSION,
)
from docparser.domain.parser_contract import ParserExecutionError, RuntimeDevice

PaddleOCRVLRuntimeError = ParserExecutionError


def _version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def installed_versions() -> dict[str, str | None]:
    framework = _version("paddlepaddle-gpu") or _version("paddlepaddle")
    return {
        "paddleocr": _version("paddleocr"),
        "paddlex": _version("paddlex"),
        "paddlepaddle": framework,
    }


def runtime_is_compatible() -> bool:
    installed = installed_versions()
    return (
        importlib.util.find_spec("paddleocr") is not None
        and importlib.util.find_spec("paddle") is not None
        and installed == {
            "paddleocr": PADDLEOCR_VERSION,
            "paddlex": PADDLEX_VERSION,
            "paddlepaddle": PADDLEPADDLE_VERSION,
        }
    )


def cuda_is_available() -> bool:
    if importlib.util.find_spec("paddle") is None:
        return False
    paddle = importlib.import_module("paddle")
    device = getattr(paddle, "device", None)
    compiled = getattr(device, "is_compiled_with_cuda", None)
    cuda = getattr(device, "cuda", None)
    count = getattr(cuda, "device_count", None)
    return bool(callable(compiled) and compiled() and callable(count) and count() > 0)


def resolve_device(requested: RuntimeDevice) -> RuntimeDevice:
    if requested is RuntimeDevice.CPU:
        return RuntimeDevice.CPU
    if requested is RuntimeDevice.CUDA:
        if not cuda_is_available():
            raise PaddleOCRVLRuntimeError(
                "CUDA was explicitly requested but PaddlePaddle GPU is unavailable",
                code="RUNTIME_UNAVAILABLE",
                retryable=True,
            )
        return RuntimeDevice.CUDA
    return RuntimeDevice.CUDA if cuda_is_available() else RuntimeDevice.CPU

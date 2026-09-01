"""Pinned Docling standard profile."""

from typing import Literal

from pydantic import Field

from docparser.domain.parser_contract import RuntimeDevice
from docparser.ir.base import StrictIRModel

DOCLING_VERSION = "2.123.0"
ADAPTER_VERSION = "0.1.0"
PROFILE_NAME = "docling-standard"


class DoclingOptions(StrictIRModel):
    device: RuntimeDevice = RuntimeDevice.AUTO
    ocr_enabled: bool = True
    ocr_engine: Literal["rapidocr"] = "rapidocr"
    ocr_languages: tuple[Literal["ch"], ...] = ("ch",)
    table_mode: Literal["accurate"] = "accurate"
    table_cell_matching: bool = True
    formula_enrichment: bool = True
    code_enrichment: bool = True
    page_batch_size: int = Field(default=1, strict=True, ge=1, le=8)
    cpu_threads: int = Field(default=4, strict=True, ge=1, le=64)


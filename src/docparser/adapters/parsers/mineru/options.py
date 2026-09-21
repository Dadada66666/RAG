"""Pinned MinerU 3.4.5 Hybrid High profile."""

from typing import Literal

from docparser.domain.parser_contract import RuntimeDevice
from docparser.ir.base import StrictIRModel

MINERU_VERSION = "3.4.5"
ADAPTER_VERSION = "0.1.3"
PROFILE_NAME = "mineru-3.4.5-hybrid-high-auto"


class MinerUOptions(StrictIRModel):
    executable: str = "mineru"
    device: RuntimeDevice = RuntimeDevice.AUTO
    backend: Literal["hybrid-engine"] = "hybrid-engine"
    effort: Literal["high"] = "high"
    parse_method: Literal["auto"] = "auto"
    model_source: Literal["local"] = "local"

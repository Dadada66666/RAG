"""Pinned PaddleOCR-VL-1.6 complete-pipeline profile."""

from typing import Literal

from docparser.domain.parser_contract import RuntimeDevice
from docparser.ir.base import StrictIRModel

PADDLEOCR_VERSION = "3.7.0"
PADDLEX_VERSION = "3.7.1"
PADDLEPADDLE_VERSION = "3.3.0"
ADAPTER_VERSION = "0.1.0"
PROFILE_NAME = "paddleocr-vl-1.6"


class PaddleOCRVLOptions(StrictIRModel):
    device: RuntimeDevice = RuntimeDevice.AUTO
    pipeline_version: Literal["v1.6"] = "v1.6"
    layout_model: Literal["PP-DocLayoutV3"] = "PP-DocLayoutV3"
    recognition_model: Literal["PaddleOCR-VL-1.6-0.9B"] = "PaddleOCR-VL-1.6-0.9B"
    recognition_backend: Literal["native"] = "native"
    use_layout_detection: bool = True
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    merge_layout_blocks: bool = True
    format_block_content: bool = False
    use_queues: bool = False

"""Optional PaddleOCR-VL-1.6 adapter."""

from docparser.adapters.parsers.paddleocr_vl.adapter import PaddleOCRVLParserAdapter
from docparser.adapters.parsers.paddleocr_vl.options import PaddleOCRVLOptions

__all__ = ["PaddleOCRVLParserAdapter", "PaddleOCRVLOptions"]

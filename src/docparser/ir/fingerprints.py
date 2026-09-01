"""Derived semantic fingerprints used only as matching and cache hints."""

from __future__ import annotations

import hashlib
import json

from docparser.ir.models import Block
from docparser.ir.types import Sha256Digest

BLOCK_FINGERPRINT_VERSION = "block-semantic-v1"


def semantic_fingerprint(block: Block) -> Sha256Digest:
    """Hash versioned block type, text, and canonical quantized geometry."""

    payload = {
        "bbox": list(block.bbox.root),
        "block_type": block.block_type.value,
        "text": block.text,
        "version": BLOCK_FINGERPRINT_VERSION,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")

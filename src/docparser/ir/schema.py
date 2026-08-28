"""Deterministic JSON Schema generation for Canonical Document IR."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docparser.ir.models import DocumentIR

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "https://schemas.docparser.local/document-ir/v1/document-ir.schema.json"
DEFAULT_SCHEMA_PATH = Path("schemas/document-ir/v1/document-ir.schema.json")


def document_ir_schema() -> dict[str, Any]:
    """Build the authoritative Draft 2020-12 schema from Pydantic models."""

    schema = DocumentIR.model_json_schema(
        mode="validation",
        ref_template="#/$defs/{model}",
    )
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = SCHEMA_ID
    return schema


def render_document_ir_schema() -> bytes:
    """Render stable, reviewable schema bytes."""

    return (
        json.dumps(document_ir_schema(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def write_document_ir_schema(path: Path = DEFAULT_SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_document_ir_schema())


def schema_is_current(path: Path = DEFAULT_SCHEMA_PATH) -> bool:
    return path.is_file() and path.read_bytes() == render_document_ir_schema()

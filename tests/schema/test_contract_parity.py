from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError
from tests.full_ir_factory import make_full_document
from tests.ir_factory import make_document

from docparser.ir.ids import DocumentId, RevisionId
from docparser.ir.schema import document_ir_schema
from docparser.ir.serialization import dump_canonical_json, load_canonical_json


def _set_path(payload: Any, path: tuple[str | int, ...], value: Any) -> None:
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value


def _runtime_accepts(payload: dict[str, Any]) -> bool:
    try:
        load_canonical_json(json.dumps(payload, ensure_ascii=False))
    except (ValidationError, ValueError):
        return False
    return True


def _schema_accepts(payload: dict[str, Any]) -> bool:
    return Draft202012Validator(document_ir_schema()).is_valid(payload)


@pytest.mark.parametrize(
    ("fixture", "path", "value"),
    [
        ("minimal", ("pages", 0, "blocks", 0, "confidence"), -0.01),
        ("minimal", ("pages", 0, "blocks", 0, "confidence"), 1.01),
        ("minimal", ("pages", 0, "page_number"), 0),
        ("minimal", ("page_count",), 1001),
        ("minimal", ("pages", 0, "width"), 0.0),
        ("full", ("tables", 0, "cells", 0, "row_span"), 0),
        ("minimal", ("source", "sha256"), f"sha256:{'A' * 64}"),
        ("minimal", ("created_at",), "2026-08-28T08:30:00+00:00"),
        ("minimal", ("pages", 0, "blocks", 0, "extensions"), {"raw": True}),
        ("minimal", ("unexpected",), True),
    ],
)
def test_critical_invalid_wire_cases_fail_runtime_and_schema(
    fixture: str,
    path: tuple[str | int, ...],
    value: Any,
) -> None:
    document = make_full_document() if fixture == "full" else make_document()
    payload = json.loads(dump_canonical_json(document))
    _set_path(payload, path, value)

    assert not _runtime_accepts(payload)
    assert not _schema_accepts(payload)


@pytest.mark.parametrize(
    ("id_type", "value", "accepted"),
    [
        (
            DocumentId,
            "doc_5818d8b2-78e6-5145-ad5c-8d4340edd378",
            True,
        ),
        (
            DocumentId,
            "doc_018bcfe5-6800-7000-8000-000000000001",
            False,
        ),
        (
            RevisionId,
            "rev_018bcfe5-6800-7000-8000-000000000001",
            True,
        ),
        (
            RevisionId,
            "rev_5818d8b2-78e6-5145-ad5c-8d4340edd378",
            False,
        ),
    ],
)
def test_uuid_version_contract_parity(
    id_type: type[DocumentId] | type[RevisionId],
    value: str,
    accepted: bool,
) -> None:
    field_name = "document_id" if id_type is DocumentId else "revision_id"
    field_schema = document_ir_schema()["properties"][field_name]
    runtime_accepted = True
    try:
        TypeAdapter(id_type).validate_python(value)
    except ValidationError:
        runtime_accepted = False

    assert runtime_accepted is accepted
    assert Draft202012Validator(field_schema).is_valid(value) is accepted


def test_generated_schema_contains_only_standard_numeric_keywords() -> None:
    invalid_keywords = {"ge", "gt", "le", "lt"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            assert invalid_keywords.isdisjoint(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(copy.deepcopy(document_ir_schema()))

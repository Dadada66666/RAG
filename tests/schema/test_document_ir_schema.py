from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from docparser.ir.schema import (
    DEFAULT_SCHEMA_PATH,
    document_ir_schema,
    render_document_ir_schema,
    schema_is_current,
)
from docparser.ir.serialization import load_canonical_json

FIXTURE_ROOT = Path("tests/schema/fixtures")
POSITIVE_FIXTURE = FIXTURE_ROOT / "positive/minimal-document.json"
NEGATIVE_CASES = FIXTURE_ROOT / "negative/wire-cases.json"


def _set_path(payload: Any, path: list[str | int], value: Any) -> None:
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value


def test_generated_schema_is_valid_draft_2020_12() -> None:
    schema = document_ir_schema()

    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_committed_schema_has_no_drift() -> None:
    assert schema_is_current(DEFAULT_SCHEMA_PATH)
    assert DEFAULT_SCHEMA_PATH.read_bytes() == render_document_ir_schema()


def test_positive_fixture_passes_runtime_and_json_schema() -> None:
    fixture_bytes = POSITIVE_FIXTURE.read_bytes()
    payload = json.loads(fixture_bytes)
    validator = Draft202012Validator(document_ir_schema())

    assert list(validator.iter_errors(payload)) == []
    assert load_canonical_json(fixture_bytes).page_count == 1


@pytest.mark.parametrize(
    "case",
    json.loads(NEGATIVE_CASES.read_text(encoding="utf-8")),
    ids=lambda case: str(case["name"]),
)
def test_negative_wire_fixture_fails_runtime_and_json_schema(case: dict[str, Any]) -> None:
    payload = json.loads(POSITIVE_FIXTURE.read_text(encoding="utf-8"))
    invalid = copy.deepcopy(payload)
    _set_path(invalid, case["path"], case["value"])
    validator = Draft202012Validator(document_ir_schema())

    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValidationError):
        load_canonical_json(json.dumps(invalid, ensure_ascii=False))

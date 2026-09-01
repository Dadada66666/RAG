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
POSITIVE_FIXTURES = (
    FIXTURE_ROOT / "positive/minimal-document.json",
    FIXTURE_ROOT / "positive/full-document.json",
)
NEGATIVE_CASES = FIXTURE_ROOT / "negative/wire-cases.json"
GRAPH_NEGATIVE_CASES = FIXTURE_ROOT / "negative/graph-cases.json"


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


@pytest.mark.parametrize("fixture_path", POSITIVE_FIXTURES, ids=lambda path: path.stem)
def test_positive_fixture_passes_runtime_and_json_schema(fixture_path: Path) -> None:
    fixture_bytes = fixture_path.read_bytes()
    payload = json.loads(fixture_bytes)
    validator = Draft202012Validator(document_ir_schema())

    assert list(validator.iter_errors(payload)) == []
    assert load_canonical_json(fixture_bytes).page_count >= 1


@pytest.mark.parametrize(
    "case",
    json.loads(NEGATIVE_CASES.read_text(encoding="utf-8")),
    ids=lambda case: str(case["name"]),
)
def test_negative_wire_fixture_fails_runtime_and_json_schema(case: dict[str, Any]) -> None:
    payload = json.loads(POSITIVE_FIXTURES[0].read_text(encoding="utf-8"))
    invalid = copy.deepcopy(payload)
    _set_path(invalid, case["path"], case["value"])
    validator = Draft202012Validator(document_ir_schema())

    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValidationError):
        load_canonical_json(json.dumps(invalid, ensure_ascii=False))


@pytest.mark.parametrize(
    "case",
    json.loads(GRAPH_NEGATIVE_CASES.read_text(encoding="utf-8")),
    ids=lambda case: str(case["name"]),
)
def test_negative_graph_fixture_passes_schema_but_fails_domain(case: dict[str, Any]) -> None:
    payload = json.loads(POSITIVE_FIXTURES[1].read_text(encoding="utf-8"))
    invalid = copy.deepcopy(payload)
    for mutation in case["mutations"]:
        _set_path(invalid, mutation["path"], mutation["value"])
    if cycle_section_id := case.get("cycle_section_id"):
        root = invalid["sections"][0]
        child = copy.deepcopy(root)
        child["section_id"] = cycle_section_id
        child["parent_section_id"] = root["section_id"]
        child["child_section_ids"] = [root["section_id"]]
        child["content_block_ids"] = []
        root["parent_section_id"] = cycle_section_id
        root["child_section_ids"] = [cycle_section_id]
        invalid["sections"].append(child)
    validator = Draft202012Validator(document_ir_schema())

    assert list(validator.iter_errors(invalid)) == []
    with pytest.raises(ValidationError, match=case["error"]):
        load_canonical_json(json.dumps(invalid, ensure_ascii=False))

from __future__ import annotations

import copy
import json
from typing import Any, cast

import pytest
from pydantic import ValidationError
from tests.full_ir_factory import make_full_document
from tests.ir_factory import TEST_NAMESPACE

from docparser.ir.ids import BlockId, ProvenanceId, SectionId, generate_uuid5_id
from docparser.ir.serialization import dump_canonical_json, load_canonical_json


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(dump_canonical_json(make_full_document())))


def _missing(id_type: type[BlockId] | type[ProvenanceId] | type[SectionId], name: str) -> str:
    return str(generate_uuid5_id(id_type, TEST_NAMESPACE, name))


def test_complete_graph_validates() -> None:
    document = load_canonical_json(dump_canonical_json(make_full_document()))

    assert len(document.pages) == 2
    assert document.tables[0].logical_row_count == 3
    assert document.chunks[0].heading_path == ("1. 财务摘要 / Financial Summary",)


def test_missing_figure_caption_block_is_rejected() -> None:
    payload = _payload()
    payload["figures"][0]["caption_block_ids"][0] = _missing(BlockId, "caption")

    with pytest.raises(ValidationError, match="caption_block_id"):
        load_canonical_json(json.dumps(payload))


def test_broken_relationship_target_is_rejected() -> None:
    payload = _payload()
    payload["relationships"][0]["target_id"] = _missing(SectionId, "target")

    with pytest.raises(ValidationError, match="relationship source or target"):
        load_canonical_json(json.dumps(payload))


def test_invalid_relationship_compatibility_is_rejected() -> None:
    payload = _payload()
    payload["relationships"][0]["type"] = "READING_NEXT"

    with pytest.raises(ValidationError, match="incompatible"):
        load_canonical_json(json.dumps(payload))


def test_reading_next_must_match_page_order() -> None:
    payload = _payload()
    reading = next(item for item in payload["relationships"] if item["type"] == "READING_NEXT")
    relationship_id = reading["relationship_id"]
    payload["pages"][1]["blocks"][2]["relationship_ids"] = []
    payload["pages"][1]["blocks"][3]["relationship_ids"] = [relationship_id]
    reading["target_id"] = payload["pages"][1]["blocks"][3]["block_id"]

    with pytest.raises(ValidationError, match="reading order"):
        load_canonical_json(json.dumps(payload))


def test_section_parent_must_resolve() -> None:
    payload = _payload()
    payload["sections"][0]["parent_section_id"] = _missing(SectionId, "parent")

    with pytest.raises(ValidationError, match="parent_section_id"):
        load_canonical_json(json.dumps(payload))


def test_valid_nested_section_hierarchy_resolves_heading_path() -> None:
    payload = _payload()
    root = payload["sections"][0]
    child = copy.deepcopy(root)
    child_id = _missing(SectionId, "valid-child")
    child["section_id"] = child_id
    child["level"] = 2
    child["heading_block_id"] = None
    child["parent_section_id"] = root["section_id"]
    child["child_section_ids"] = []
    child["content_block_ids"] = []
    child["page_start"] = 2
    child["page_end"] = 2
    root["child_section_ids"] = [child_id]
    payload["sections"].append(child)

    document = load_canonical_json(json.dumps(payload))

    assert document.sections[1].parent_section_id == document.sections[0].section_id


def test_section_cycle_is_rejected() -> None:
    payload = _payload()
    root = payload["sections"][0]
    child = copy.deepcopy(root)
    child_id = _missing(SectionId, "cycle-child")
    child["section_id"] = child_id
    child["parent_section_id"] = root["section_id"]
    child["child_section_ids"] = [root["section_id"]]
    child["content_block_ids"] = []
    root["parent_section_id"] = child_id
    root["child_section_ids"] = [child_id]
    payload["sections"].append(child)

    with pytest.raises(ValidationError, match="section graph contains a cycle"):
        load_canonical_json(json.dumps(payload))


def test_section_page_range_must_exist() -> None:
    payload = _payload()
    payload["sections"][0]["page_end"] = 3

    with pytest.raises(ValidationError, match="section page range"):
        load_canonical_json(json.dumps(payload))


def test_table_segment_block_must_resolve() -> None:
    payload = _payload()
    payload["tables"][0]["segments"][0]["block_id"] = _missing(BlockId, "segment")

    with pytest.raises(ValidationError, match="table segment block_id"):
        load_canonical_json(json.dumps(payload))


def test_chunk_source_block_must_resolve() -> None:
    payload = _payload()
    payload["chunks"][0]["source_block_ids"][0] = _missing(BlockId, "chunk")

    with pytest.raises(ValidationError, match="chunk source_block_id"):
        load_canonical_json(json.dumps(payload))


def test_new_entity_provenance_must_resolve() -> None:
    payload = _payload()
    payload["equations"][0]["provenance_ids"][0] = _missing(ProvenanceId, "equation")

    with pytest.raises(ValidationError, match="provenance reference"):
        load_canonical_json(json.dumps(payload))

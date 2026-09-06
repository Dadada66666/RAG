from __future__ import annotations

import copy
import json
from typing import Any, cast

import pytest
from pydantic import ValidationError
from tests.full_ir_factory import make_full_document
from tests.ir_factory import TEST_NAMESPACE

from docparser.ir.ids import (
    BlockId,
    DocumentId,
    ProvenanceId,
    SectionId,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.serialization import dump_canonical_json, load_canonical_json


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(dump_canonical_json(make_full_document())))


def _missing(id_type: type[BlockId] | type[ProvenanceId] | type[SectionId], name: str) -> str:
    return str(generate_uuid5_id(id_type, TEST_NAMESPACE, name))


def _three_page_section_payload() -> dict[str, Any]:
    payload = _payload()
    page_three_provenance = _missing(ProvenanceId, "section-page-three")
    page_three = copy.deepcopy(payload["pages"][1])
    page_three["page_id"] = str(generate_page_id(DocumentId(payload["document_id"]), 3))
    page_three["page_number"] = 3
    page_three["blocks"] = []
    page_three["provenance_ids"] = [page_three_provenance]
    provenance = copy.deepcopy(
        next(
            record
            for record in payload["provenance"]
            if record["page_number"] == 2 and not record["parent_provenance_ids"]
        )
    )
    provenance["provenance_id"] = page_three_provenance
    provenance["page_number"] = 3
    provenance["original_object_id"] = "page:3"
    payload["page_count"] = 3
    payload["pages"].append(page_three)
    payload["provenance"].append(provenance)
    payload["chunks"] = []
    return payload


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


def test_sibling_sections_may_share_a_physical_page() -> None:
    payload = _three_page_section_payload()
    first = payload["sections"][0]
    second = copy.deepcopy(first)
    second_id = _missing(SectionId, "shared-page-sibling")
    page_one_blocks = payload["pages"][0]["blocks"]
    page_two_blocks = payload["pages"][1]["blocks"]
    first.update(
        {
            "heading_block_id": page_one_blocks[0]["block_id"],
            "content_block_ids": [block["block_id"] for block in page_one_blocks[1:]],
            "page_start": 1,
            "page_end": 2,
            "child_section_ids": [],
        }
    )
    second.update(
        {
            "section_id": second_id,
            "heading_block_id": page_two_blocks[1]["block_id"],
            "content_block_ids": [
                block["block_id"] for index, block in enumerate(page_two_blocks) if index != 1
            ],
            "page_start": 2,
            "page_end": 3,
            "child_section_ids": [],
            "provenance_ids": payload["pages"][1]["provenance_ids"],
        }
    )
    payload["sections"] = [first, second]

    document = load_canonical_json(json.dumps(payload))

    assert [(section.page_start, section.page_end) for section in document.sections] == [
        (1, 2),
        (2, 3),
    ]


def test_child_section_span_must_be_contained_by_parent() -> None:
    payload = _three_page_section_payload()
    parent = payload["sections"][0]
    child = copy.deepcopy(parent)
    child_id = _missing(SectionId, "outside-parent")
    parent["page_start"] = 1
    parent["page_end"] = 2
    parent["child_section_ids"] = [child_id]
    child.update(
        {
            "section_id": child_id,
            "parent_section_id": parent["section_id"],
            "child_section_ids": [],
            "heading_block_id": None,
            "content_block_ids": [],
            "page_start": 3,
            "page_end": 3,
            "provenance_ids": payload["pages"][2]["provenance_ids"],
        }
    )
    payload["sections"] = [parent, child]

    with pytest.raises(ValidationError, match="nested in parent"):
        load_canonical_json(json.dumps(payload))


def test_section_parent_child_refs_must_be_reciprocal() -> None:
    payload = _payload()
    parent = payload["sections"][0]
    child = copy.deepcopy(parent)
    child["section_id"] = _missing(SectionId, "nonreciprocal-child")
    child["parent_section_id"] = parent["section_id"]
    child["child_section_ids"] = []
    child["heading_block_id"] = None
    child["content_block_ids"] = []
    payload["sections"].append(child)

    with pytest.raises(ValidationError, match="reciprocal"):
        load_canonical_json(json.dumps(payload))


def test_section_content_block_has_one_direct_owner() -> None:
    payload = _payload()
    first = payload["sections"][0]
    second = copy.deepcopy(first)
    second["section_id"] = _missing(SectionId, "duplicate-owner")
    second["heading_block_id"] = None
    second["child_section_ids"] = []
    payload["sections"].append(second)

    with pytest.raises(ValidationError, match="multiple direct owners"):
        load_canonical_json(json.dumps(payload))


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

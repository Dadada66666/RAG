from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError
from tests.full_ir_factory import make_full_document

from docparser.ir.serialization import dump_canonical_json, load_canonical_json


def _payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(dump_canonical_json(make_full_document())))


def test_relationship_ids_are_unique_document_wide() -> None:
    payload = _payload()
    payload["relationships"][1]["relationship_id"] = payload["relationships"][0][
        "relationship_id"
    ]

    with pytest.raises(ValidationError, match="relationship IDs must be unique"):
        load_canonical_json(json.dumps(payload))


def test_provenance_lineage_cycle_is_rejected() -> None:
    payload = _payload()
    first_id = payload["provenance"][0]["provenance_id"]
    second_id = payload["provenance"][1]["provenance_id"]
    payload["provenance"][0]["parent_provenance_ids"] = [second_id]
    payload["provenance"][1]["parent_provenance_ids"] = [first_id]

    with pytest.raises(ValidationError, match="provenance lineage contains a cycle"):
        load_canonical_json(json.dumps(payload))


def test_source_artifact_must_be_in_processing_manifest() -> None:
    payload = _payload()
    payload["processing"]["artifact_ids"] = payload["processing"]["artifact_ids"][1:]

    with pytest.raises(ValidationError, match="source artifact"):
        load_canonical_json(json.dumps(payload))


def test_block_parent_graph_cycle_is_rejected() -> None:
    payload = _payload()
    first = payload["pages"][0]["blocks"][0]
    second = payload["pages"][0]["blocks"][1]
    first["parent_block_id"] = second["block_id"]
    second["parent_block_id"] = first["block_id"]

    with pytest.raises(ValidationError, match="parent_block_id graph contains a cycle"):
        load_canonical_json(json.dumps(payload))


def test_figure_asset_must_be_in_processing_manifest() -> None:
    payload = _payload()
    payload["figures"][0]["asset_artifact_ids"] = [
        "art_018bcfe5-6800-7000-8000-000000000099"
    ]

    with pytest.raises(ValidationError, match="figure asset_artifact_id"):
        load_canonical_json(json.dumps(payload))


def test_reference_source_block_must_resolve() -> None:
    payload = _payload()
    payload["references"][0]["source_block_ids"] = [
        "blk_bb632dca-dfb7-5650-80dc-26ab96643e2b"
    ]

    with pytest.raises(ValidationError, match="reference source_block_id"):
        load_canonical_json(json.dumps(payload))


def test_chunk_heading_path_must_match_section_hierarchy() -> None:
    payload = _payload()
    payload["chunks"][0]["heading_path"] = ["Unrelated heading"]

    with pytest.raises(ValidationError, match="heading_path"):
        load_canonical_json(json.dumps(payload))


def test_chunk_content_types_must_match_source_blocks() -> None:
    payload = _payload()
    payload["chunks"][0]["content_types"] = ["PARAGRAPH"]

    with pytest.raises(ValidationError, match="content_types"):
        load_canonical_json(json.dumps(payload))

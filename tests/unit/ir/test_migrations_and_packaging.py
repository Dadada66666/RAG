from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.full_ir_factory import make_full_document
from tests.ir_factory import make_document

from docparser.ir.chunks import (
    GlobalReferenceEntry,
    IRPackagingManifest,
    IRShardDescriptor,
)
from docparser.ir.enums import IRShardKind
from docparser.ir.fingerprints import semantic_fingerprint
from docparser.ir.ids import ArtifactId
from docparser.ir.migrations import migrate_ir
from docparser.ir.serialization import dump_canonical_json, load_canonical_json, semantic_digest
from docparser.ir.types import Sha256Digest


def test_noop_migration_is_pure_idempotent_and_digest_preserving() -> None:
    document = make_full_document()
    payload = json.loads(dump_canonical_json(document))

    first = migrate_ir("1.2.0", "1.2.0", payload)
    second = migrate_ir("1.2.0", "1.2.0", first)

    assert first == second == payload
    assert first is not payload
    assert semantic_digest(load_canonical_json(json.dumps(first))) == semantic_digest(document)


def test_migration_rejects_unknown_route_and_mismatched_payload() -> None:
    payload = json.loads(dump_canonical_json(make_document()))

    with pytest.raises(ValueError, match="unsupported"):
        migrate_ir("1.1.0", "2.0.0", payload)
    with pytest.raises(ValueError, match="does not match"):
        migrate_ir("1.0.0", "1.0.0", {**payload, "schema_version": "0.9.0"})


def test_v1_0_quality_contract_migrates_to_v1_1() -> None:
    payload = json.loads(dump_canonical_json(make_document()))
    payload["schema_version"] = "1.0.0"

    migrated = migrate_ir("1.0.0", "1.1.0", payload)

    assert migrated["schema_version"] == "1.1.0"
    assert load_canonical_json(json.dumps(payload)).schema_version == "1.2.0"


def test_v1_1_migrates_deterministically_to_v1_2_without_rewriting_quality() -> None:
    payload = json.loads(dump_canonical_json(make_document()))
    payload["schema_version"] = "1.1.0"
    quality = payload["quality_summary"].copy()

    migrated = migrate_ir("1.1.0", "1.2.0", payload)

    assert migrated["schema_version"] == "1.2.0"
    assert migrated["quality_summary"] == quality
    assert payload["schema_version"] == "1.1.0"


def test_semantic_fingerprint_is_derived_and_checked() -> None:
    document = make_document()
    block = document.pages[0].blocks[0]
    fingerprint = semantic_fingerprint(block)
    fingerprinted = block.model_copy(update={"semantic_fingerprint": fingerprint})

    make_document(blocks=(fingerprinted,))
    changed = fingerprinted.model_copy(update={"text": "changed"})
    with pytest.raises(ValidationError, match="semantic_fingerprint"):
        make_document(blocks=(changed,))


def test_packaging_manifest_indexes_entities_to_shards() -> None:
    document = make_full_document()
    artifact_id = ArtifactId("art_018bcfe5-6800-7000-8000-000000000005")
    digest = Sha256Digest(f"sha256:{'d' * 64}")
    manifest = IRPackagingManifest(
        packaging_version="1.0.0",
        schema_version="1.0.0",
        document_id=document.document_id,
        revision_id=document.revision_id,
        semantic_digest=semantic_digest(document),
        shards=(
            IRShardDescriptor(
                kind=IRShardKind.PAGES,
                page_start=1,
                page_end=2,
                artifact_id=artifact_id,
                digest=digest,
                count=2,
            ),
        ),
        global_reference_index=(
            GlobalReferenceEntry(entity_id=document.pages[0].page_id, shard_index=0),
        ),
    )

    assert manifest.global_reference_index[0].shard_index == 0
    payload = json.loads(manifest.model_dump_json())
    payload["global_reference_index"][0]["shard_index"] = 1
    with pytest.raises(ValidationError, match="shard_index"):
        IRPackagingManifest.model_validate_json(json.dumps(payload))

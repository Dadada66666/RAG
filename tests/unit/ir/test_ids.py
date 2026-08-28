from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.ir_factory import TEST_NAMESPACE

from docparser.ir.ids import (
    DocumentId,
    PageId,
    RevisionId,
    RevisionIdGenerator,
    build_uuid7,
    generate_document_id,
    generate_page_id,
    generate_uuid5_id,
)
from docparser.ir.types import Sha256Digest


def test_document_id_is_deterministic_and_tenant_scoped() -> None:
    digest = Sha256Digest(f"sha256:{'a' * 64}")

    first = generate_document_id(TEST_NAMESPACE, "tenant-a", digest)
    second = generate_document_id(TEST_NAMESPACE, "tenant-a", digest)
    other_tenant = generate_document_id(TEST_NAMESPACE, "tenant-b", digest)

    assert first == second
    assert first != other_tenant
    assert UUID(first.removeprefix("doc_")).version == 5


@given(page_number=st.integers(min_value=1, max_value=1000))
def test_page_id_is_deterministic(page_number: int) -> None:
    document_id = generate_document_id(
        TEST_NAMESPACE,
        "tenant",
        Sha256Digest(f"sha256:{'c' * 64}"),
    )

    assert generate_page_id(document_id, page_number) == generate_page_id(
        document_id,
        page_number,
    )


def test_uuid5_component_encoding_has_no_separator_collision() -> None:
    first = generate_uuid5_id(PageId, TEST_NAMESPACE, "a|b", "c")
    second = generate_uuid5_id(PageId, TEST_NAMESPACE, "a", "b|c")

    assert first != second


def test_revision_generator_uses_injected_uuid7_inputs() -> None:
    generator = RevisionIdGenerator(
        clock_ms=lambda: 1_700_000_000_000,
        entropy=lambda bits: 1,
    )

    revision_id = generator.new()
    payload = UUID(revision_id.removeprefix("rev_"))

    assert revision_id == RevisionId("rev_018bcfe5-6800-7000-8000-000000000001")
    assert payload.version == 7
    assert payload.variant == "specified in RFC 4122"


@pytest.mark.parametrize(
    "value",
    [
        "doc_not-a-uuid",
        "page_018bcfe5-6800-7000-8000-000000000001",
        "doc_4F910F30-7F53-5F29-BD9F-867AD93DB4A6",
        "wrong_4f910f30-7f53-5f29-bd9f-867ad93db4a6",
    ],
)
def test_invalid_typed_ids_are_rejected(value: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        DocumentId(value)


@pytest.mark.parametrize("timestamp_ms", [-1, 1 << 48])
def test_uuid7_rejects_out_of_range_timestamp(timestamp_ms: int) -> None:
    with pytest.raises(ValueError, match="48 bits"):
        build_uuid7(timestamp_ms, 0)


def test_page_number_must_be_integer() -> None:
    document_id = generate_document_id(
        TEST_NAMESPACE,
        "tenant",
        Sha256Digest(f"sha256:{'d' * 64}"),
    )

    with pytest.raises(ValueError, match="integer"):
        generate_page_id(document_id, True)

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from docparser.evaluation.ohr import (
    OHRSelectionConfig,
    OHRSourceQuestion,
    RetrievalEvidenceType,
    load_ohr_questions,
    map_ohr_evidence_type,
    prepare_ohr_rag_core,
    write_ohr_subset,
)
from docparser.ir.types import UtcTimestamp

FIXTURE = Path("tests/fixtures/ohr/synthetic-qas.json")
CREATED_AT = UtcTimestamp("2026-01-01T00:00:00Z")


def _item(
    document: str,
    index: int,
    evidence_source: str,
    *,
    domain: str = "finance",
) -> dict[str, object]:
    return {
        "doc_name": document,
        "ID": f"{document}:{evidence_source}:{index}",
        "questions": f"Synthetic question {index}?",
        "answers": f"Synthetic answer {index}",
        "doc_type": domain,
        "answer_form": "String",
        "evidence_source": evidence_source,
        "evidence_context": f"Synthetic evidence {index}.",
        "evidence_page_no": index + 1,
    }


def _write_dataset(root: Path, items: list[dict[str, object]]) -> Path:
    qa_path = root / "data" / "qas_v2.json"
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return root


def _targets(*, text: int, table: int, reading_order: int) -> dict[RetrievalEvidenceType, int]:
    return {
        RetrievalEvidenceType.TEXT: text,
        RetrievalEvidenceType.TABLE: table,
        RetrievalEvidenceType.READING_ORDER: reading_order,
    }


def test_real_wire_evidence_mapping_is_explicit() -> None:
    assert map_ohr_evidence_type("text") is RetrievalEvidenceType.TEXT
    assert map_ohr_evidence_type("table") is RetrievalEvidenceType.TABLE
    assert map_ohr_evidence_type("reading_order") is RetrievalEvidenceType.READING_ORDER
    assert map_ohr_evidence_type("formula") is None
    assert map_ohr_evidence_type("chart") is None
    assert map_ohr_evidence_type("multi") is None

    with pytest.raises(ValueError, match="unknown OHR evidence_source: 'TXT'"):
        map_ohr_evidence_type("TXT")
    with pytest.raises(ValueError, match="unknown OHR evidence_source: 'something_new'"):
        map_ohr_evidence_type("something_new")


def test_synthetic_fixture_excludes_unsupported_evidence(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    qa_path = root / "data" / "qas_v2.json"
    qa_path.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE, qa_path)

    subset = prepare_ohr_rag_core(
        dataset_root=root,
        config=OHRSelectionConfig(
            max_documents=3,
            target_query_counts=_targets(text=1, table=1, reading_order=1),
        ),
        created_at=CREATED_AT,
    )

    assert {query.evidence_type for query in subset.queries} == set(RetrievalEvidenceType)
    assert subset.manifest.excluded_query_count_by_evidence_source == {
        "chart": 1,
        "formula": 1,
        "multi": 1,
    }
    text = next(
        query for query in subset.queries if query.evidence_type is RetrievalEvidenceType.TEXT
    )
    table = next(
        query for query in subset.queries if query.evidence_type is RetrievalEvidenceType.TABLE
    )
    reading_order = next(
        query
        for query in subset.queries
        if query.evidence_type is RetrievalEvidenceType.READING_ORDER
    )
    assert text.evidence_contexts == (
        "This is an invented paragraph with an example phrase.",
    )
    assert text.evidence_page_indices == (24,)
    assert table.evidence_contexts == (
        "Synthetic metric header",
        "Synthetic metric | 42",
    )
    assert table.evidence_page_indices == (1, 2)
    assert reading_order.evidence_contexts == (
        "Synthetic ordering context part A.",
        "Step A precedes Step B in this synthetic example.",
    )
    assert reading_order.evidence_page_indices == (0, 2)


def test_negative_source_page_index_is_rejected() -> None:
    payload = _item("manual/invalid", 1, "text", domain="manual")
    payload["evidence_page_no"] = -1

    with pytest.raises(ValidationError):
        OHRSourceQuestion.model_validate(payload)


def test_document_selection_prefers_evidence_coverage_then_domain_diversity(
    tmp_path: Path,
) -> None:
    items = [
        *[_item("finance/text-only", index, "text") for index in range(8)],
        _item("finance/mixed-a", 20, "text"),
        _item("finance/mixed-a", 21, "table"),
        _item("finance/mixed-a", 22, "reading_order"),
        _item("finance/mixed-b", 30, "text"),
        _item("finance/mixed-b", 31, "table"),
        _item("finance/mixed-b", 32, "reading_order"),
        _item("law/mixed-c", 40, "text", domain="law"),
        _item("law/mixed-c", 41, "table", domain="law"),
        _item("law/mixed-c", 42, "reading_order", domain="law"),
    ]
    root = _write_dataset(tmp_path / "dataset", items)
    subset = prepare_ohr_rag_core(
        dataset_root=root,
        config=OHRSelectionConfig(
            max_documents=2,
            target_query_counts=_targets(text=2, table=2, reading_order=2),
        ),
        created_at=CREATED_AT,
    )

    assert "finance/text-only" not in subset.manifest.selected_document_names
    selected = {document.document_name for document in subset.required_documents}
    assert "law/mixed-c" in selected
    assert len(selected & {"finance/mixed-a", "finance/mixed-b"}) == 1


def test_document_and_per_document_caps_are_enforced(tmp_path: Path) -> None:
    items = [
        _item(f"domain/document-{document}", index, "text", domain=f"domain-{document}")
        for document in range(12)
        for index in range(20)
    ]
    root = _write_dataset(tmp_path / "dataset", items)
    subset = prepare_ohr_rag_core(
        dataset_root=root,
        config=OHRSelectionConfig(
            target_query_counts=_targets(text=200, table=0, reading_order=0)
        ),
        created_at=CREATED_AT,
    )

    counts: dict[str, int] = {}
    for query in subset.queries:
        counts[query.document_name] = counts.get(query.document_name, 0) + 1
    assert subset.manifest.selected_document_count == 10
    assert max(counts.values()) == 12
    assert len(subset.queries) == 120
    with pytest.raises(ValidationError):
        OHRSelectionConfig(max_documents=11)
    with pytest.raises(ValidationError):
        OHRSelectionConfig(max_queries_per_document=13)


def test_default_evidence_quotas_produce_100_queries(tmp_path: Path) -> None:
    items: list[dict[str, object]] = []
    for document in range(10):
        name = f"domain-{document % 3}/document-{document}"
        for index in range(4):
            items.append(
                _item(name, document * 100 + index, "text", domain=f"domain-{document % 3}")
            )
            items.append(
                _item(
                    name,
                    document * 100 + 10 + index,
                    "table",
                    domain=f"domain-{document % 3}",
                )
            )
        for index in range(2):
            items.append(
                _item(
                    name,
                    document * 100 + 20 + index,
                    "reading_order",
                    domain=f"domain-{document % 3}",
                )
            )
    root = _write_dataset(tmp_path / "dataset", items)

    subset = prepare_ohr_rag_core(dataset_root=root, created_at=CREATED_AT)

    assert subset.manifest.selected_query_count == 100
    assert subset.manifest.query_count_by_evidence_type == _targets(
        text=40,
        table=40,
        reading_order=20,
    )
    assert subset.manifest.shortfalls == ()


def test_quota_shortfall_is_explicit(tmp_path: Path) -> None:
    root = _write_dataset(
        tmp_path / "dataset",
        [
            _item("manual/short", 1, "text", domain="manual"),
            _item("manual/short", 2, "table", domain="manual"),
        ],
    )
    subset = prepare_ohr_rag_core(
        dataset_root=root,
        config=OHRSelectionConfig(
            target_query_counts=_targets(text=2, table=2, reading_order=1)
        ),
        created_at=CREATED_AT,
    )

    observed_shortfalls = [
        (item.evidence_type, item.requested, item.actual)
        for item in subset.manifest.shortfalls
    ]
    assert observed_shortfalls == [
        (RetrievalEvidenceType.TEXT, 2, 1),
        (RetrievalEvidenceType.TABLE, 2, 1),
        (RetrievalEvidenceType.READING_ORDER, 1, 0),
    ]
    assert all(
        "insufficient eligible queries" in item.reason
        for item in subset.manifest.shortfalls
    )


def test_identity_order_and_digest_are_path_independent(tmp_path: Path) -> None:
    items = [
        _item("finance/a", 1, "text"),
        _item("finance/a", 2, "table"),
        _item("manual/b", 3, "reading_order", domain="manual"),
    ]
    items[0]["evidence_context"] = ["Synthetic part A.", "Synthetic part B."]
    items[0]["evidence_page_no"] = [0, 2]
    first_root = _write_dataset(tmp_path / "first" / "dataset", items)
    second_root = _write_dataset(tmp_path / "second" / "other-name", list(reversed(items)))
    config = OHRSelectionConfig(target_query_counts=_targets(text=1, table=1, reading_order=1))

    first = prepare_ohr_rag_core(
        dataset_root=first_root,
        config=config,
        source_dataset_revision="synthetic-v1",
        source_commit="commit-a",
        created_at=UtcTimestamp("2026-01-01T00:00:00Z"),
    )
    repeated = prepare_ohr_rag_core(
        dataset_root=first_root,
        config=config,
        source_dataset_revision="synthetic-v1",
        source_commit="commit-b",
        created_at=UtcTimestamp("2026-01-02T00:00:00Z"),
    )
    relocated = prepare_ohr_rag_core(
        dataset_root=second_root,
        config=config,
        source_dataset_revision="synthetic-v1",
        created_at=UtcTimestamp("2026-01-03T00:00:00Z"),
    )

    assert first.queries == repeated.queries == relocated.queries
    assert first.required_documents == repeated.required_documents == relocated.required_documents
    assert first.manifest.selection_digest == repeated.manifest.selection_digest
    assert first.manifest.selection_digest == relocated.manifest.selection_digest
    assert first.manifest.created_at != repeated.manifest.created_at
    assert [query.benchmark_query_id for query in first.queries] == [
        query.benchmark_query_id for query in repeated.queries
    ]


def test_ground_truth_is_chunking_independent_and_artifacts_are_consistent(
    tmp_path: Path,
) -> None:
    root = _write_dataset(
        tmp_path / "dataset",
        [
            _item("finance/a", 1, "text"),
            _item("finance/a", 2, "table"),
            _item("manual/b", 3, "reading_order", domain="manual"),
        ],
    )
    subset = prepare_ohr_rag_core(
        dataset_root=root,
        config=OHRSelectionConfig(target_query_counts=_targets(text=1, table=1, reading_order=1)),
        created_at=CREATED_AT,
    )
    output = tmp_path / "external-output"

    write_ohr_subset(subset, output)

    query_payloads = [
        json.loads(line)
        for line in (output / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    forbidden = {"chunk_id", "block_id", "canonical_block_id", "section_id", "table_id"}
    assert all(not forbidden.intersection(payload) for payload in query_payloads)
    assert all("evidence_context" not in payload for payload in query_payloads)
    assert all("evidence_page_numbers" not in payload for payload in query_payloads)
    assert all("evidence_contexts" in payload for payload in query_payloads)
    assert all("evidence_page_indices" in payload for payload in query_payloads)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    required = json.loads((output / "required_documents.json").read_text(encoding="utf-8"))
    assert manifest["selected_query_count"] == len(query_payloads)
    assert manifest["selected_document_count"] == len(required)
    assert set(manifest["selected_document_names"]) == {
        document["document_name"] for document in required
    }
    assert all(document["relative_source_path"] is None for document in required)
    assert all(document["expected_source_digest"] is None for document in required)


def test_missing_dataset_root_and_unknown_wire_value_fail_clearly(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError, match="OHR dataset root not found"):
        load_ohr_questions(missing)

    root = _write_dataset(tmp_path / "dataset", [_item("finance/a", 1, "TXT")])
    with pytest.raises(ValueError, match="unknown OHR evidence_source: 'TXT'"):
        prepare_ohr_rag_core(dataset_root=root, created_at=CREATED_AT)

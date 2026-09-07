"""Deterministic preparation of the external OHR-Bench retrieval subset."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, model_validator

from docparser.ir.base import NonNegativeInt, StrictIRModel
from docparser.ir.types import NfcString, NonEmptyNfcString, Sha256Digest, UtcTimestamp

OHR_BENCHMARK_ID = "ohr-rag-core-v1"
OHR_SOURCE_DATASET = "OHR-Bench"
OHR_SELECTION_POLICY_VERSION = "ohr-rag-core-v1@1.2.0"
OHR_QA_RELATIVE_PATH = Path("data/qas_v2.json")


class RetrievalEvidenceType(StrEnum):
    """Evidence families supported by the first retrieval experiment."""

    TEXT = "TEXT"
    TABLE = "TABLE"
    READING_ORDER = "READING_ORDER"


_OHR_SUPPORTED_EVIDENCE = {
    "text": RetrievalEvidenceType.TEXT,
    "table": RetrievalEvidenceType.TABLE,
    "reading_order": RetrievalEvidenceType.READING_ORDER,
}
_OHR_EXCLUDED_EVIDENCE = frozenset({"formula", "chart", "multi"})
_EVIDENCE_ORDER: tuple[RetrievalEvidenceType, ...] = (
    RetrievalEvidenceType.TEXT,
    RetrievalEvidenceType.TABLE,
    RetrievalEvidenceType.READING_ORDER,
)


class OHRSourceQuestion(StrictIRModel):
    """Pinned public ``data/qas_v2.json`` item contract."""

    document_name: NonEmptyNfcString = Field(alias="doc_name")
    source_dataset_item_id: NonEmptyNfcString = Field(alias="ID")
    question: NonEmptyNfcString = Field(alias="questions")
    answer: NonEmptyNfcString = Field(alias="answers")
    document_type_or_domain: NonEmptyNfcString = Field(alias="doc_type")
    answer_form: NonEmptyNfcString
    evidence_source: NonEmptyNfcString
    evidence_contexts: NfcString | list[NfcString] = Field(alias="evidence_context")
    evidence_page_indices: (
        Annotated[int, Field(strict=True, ge=0)]
        | list[Annotated[int, Field(strict=True, ge=0)]]
    ) = Field(
        alias="evidence_page_no"
    )


class OHRSelectionConfig(StrictIRModel):
    """Frozen small-corpus selection policy inputs."""

    max_documents: Annotated[int, Field(strict=True, ge=1, le=10)] = 10
    max_queries_per_document: Annotated[int, Field(strict=True, ge=1, le=12)] = 12
    target_query_counts: dict[RetrievalEvidenceType, NonNegativeInt] = Field(
        default_factory=lambda: {
            RetrievalEvidenceType.TEXT: 40,
            RetrievalEvidenceType.TABLE: 40,
            RetrievalEvidenceType.READING_ORDER: 20,
        }
    )
    supported_evidence_types: tuple[RetrievalEvidenceType, ...] = _EVIDENCE_ORDER

    @model_validator(mode="after")
    def _frozen_evidence_contract(self) -> Self:
        expected = set(_EVIDENCE_ORDER)
        if self.supported_evidence_types != _EVIDENCE_ORDER:
            raise ValueError("supported_evidence_types must use the frozen OHR core evidence order")
        if set(self.target_query_counts) != expected:
            raise ValueError("target_query_counts must contain TEXT, TABLE, and READING_ORDER")
        return self


class RetrievalGroundTruth(StrictIRModel):
    """Chunking-independent truth preserving OHR's 0-based source page indices."""

    benchmark_query_id: NonEmptyNfcString
    source_dataset: NonEmptyNfcString
    source_dataset_item_id: NonEmptyNfcString
    document_name: NonEmptyNfcString
    question: NonEmptyNfcString
    answer: NonEmptyNfcString
    evidence_type: RetrievalEvidenceType
    evidence_contexts: tuple[NfcString, ...]
    evidence_page_indices: tuple[Annotated[int, Field(strict=True, ge=0)], ...]
    document_type_or_domain: NonEmptyNfcString


class RequiredDocument(StrictIRModel):
    """External source document required by a prepared subset."""

    document_name: NonEmptyNfcString
    source_dataset: NonEmptyNfcString
    source_dataset_identity: NonEmptyNfcString
    source_dataset_item_ids: tuple[NonEmptyNfcString, ...]
    relative_source_path: NfcString | None = None
    expected_source_digest: Sha256Digest | None = None


class EvidenceShortfall(StrictIRModel):
    evidence_type: RetrievalEvidenceType
    requested: NonNegativeInt
    actual: NonNegativeInt
    reason: NonEmptyNfcString


class OHRSubsetManifest(StrictIRModel):
    """Reproducibility metadata for the external subset artifacts."""

    benchmark_id: NonEmptyNfcString
    source_dataset: NonEmptyNfcString
    source_dataset_revision: NonEmptyNfcString | None = None
    source_dataset_digest: Sha256Digest
    selection_policy_version: NonEmptyNfcString
    selection_config: OHRSelectionConfig
    selected_document_count: NonNegativeInt
    selected_query_count: NonNegativeInt
    query_count_by_evidence_type: dict[RetrievalEvidenceType, NonNegativeInt]
    document_count_by_domain: dict[str, NonNegativeInt]
    selected_document_names: tuple[NonEmptyNfcString, ...]
    excluded_query_count_by_evidence_source: dict[str, NonNegativeInt]
    shortfalls: tuple[EvidenceShortfall, ...]
    selection_digest: Sha256Digest
    source_commit: NonEmptyNfcString | None = None
    created_at: UtcTimestamp


class PreparedOHRSubset(StrictIRModel):
    queries: tuple[RetrievalGroundTruth, ...]
    manifest: OHRSubsetManifest
    required_documents: tuple[RequiredDocument, ...]


def map_ohr_evidence_type(value: str) -> RetrievalEvidenceType | None:
    """Map the pinned OHR wire value; known unsupported families are excluded."""

    if value in _OHR_SUPPORTED_EVIDENCE:
        return _OHR_SUPPORTED_EVIDENCE[value]
    if value in _OHR_EXCLUDED_EVIDENCE:
        return None
    raise ValueError(f"unknown OHR evidence_source: {value!r}")


def load_ohr_questions(dataset_root: Path) -> tuple[OHRSourceQuestion, ...]:
    """Load the official local QA file without network or download behavior."""

    if not dataset_root.is_dir():
        raise FileNotFoundError(f"OHR dataset root not found: {dataset_root}")
    qa_path = dataset_root / OHR_QA_RELATIVE_PATH
    if not qa_path.is_file():
        raise FileNotFoundError(f"OHR QA file not found: {qa_path}")
    raw = json.loads(qa_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("OHR data/qas_v2.json must contain a JSON array")
    questions = tuple(OHRSourceQuestion.model_validate(item) for item in raw)
    identities = [question.source_dataset_item_id for question in questions]
    if len(identities) != len(set(identities)):
        raise ValueError("OHR data/qas_v2.json contains duplicate ID values")
    return questions


def _stable_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _source_dataset_digest(questions: tuple[OHRSourceQuestion, ...]) -> Sha256Digest:
    payload = [
        item.model_dump(mode="json", by_alias=True)
        for item in sorted(questions, key=lambda item: item.source_dataset_item_id)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def _query_id(item: OHRSourceQuestion) -> str:
    digest = _stable_key(OHR_SOURCE_DATASET, item.source_dataset_item_id)
    return f"ohrq-{digest[:32]}"


def _normalized_contexts(value: str | list[str]) -> tuple[str, ...]:
    return tuple(value) if isinstance(value, list) else (value,)


def _normalized_page_indices(value: int | list[int]) -> tuple[int, ...]:
    return tuple(value) if isinstance(value, list) else (value,)


def _eligible_items(
    questions: tuple[OHRSourceQuestion, ...],
) -> tuple[
    tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...],
    dict[str, int],
]:
    eligible: list[tuple[OHRSourceQuestion, RetrievalEvidenceType]] = []
    excluded: Counter[str] = Counter()
    for question in questions:
        evidence_type = map_ohr_evidence_type(question.evidence_source)
        if evidence_type is None:
            excluded[question.evidence_source] += 1
        else:
            eligible.append((question, evidence_type))
    return tuple(eligible), dict(sorted(excluded.items()))


def _document_catalog(
    eligible: tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...],
) -> tuple[
    dict[str, list[tuple[OHRSourceQuestion, RetrievalEvidenceType]]],
    dict[str, str],
]:
    by_document: dict[str, list[tuple[OHRSourceQuestion, RetrievalEvidenceType]]] = defaultdict(
        list
    )
    for item in eligible:
        by_document[item[0].document_name].append(item)

    domains: dict[str, str] = {}
    for document_name, items in by_document.items():
        document_domains = {item.document_type_or_domain for item, _ in items}
        if len(document_domains) != 1:
            raise ValueError(f"OHR document has conflicting doc_type values: {document_name}")
        domains[document_name] = next(iter(document_domains))
    return by_document, domains


def _ordered_document_names(document_names: tuple[str, ...] | set[str]) -> tuple[str, ...]:
    return tuple(sorted(document_names, key=lambda name: (_stable_key(name), name)))


def _allocate_query_counts(
    by_document: dict[str, list[tuple[OHRSourceQuestion, RetrievalEvidenceType]]],
    document_names: tuple[str, ...],
    config: OHRSelectionConfig,
) -> dict[tuple[str, RetrievalEvidenceType], int]:
    source = ("source", "")
    sink = ("sink", "")
    residual: dict[tuple[str, str], dict[tuple[str, str], int]] = defaultdict(dict)
    adjacency: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)

    def add_edge(start: tuple[str, str], end: tuple[str, str], capacity: int) -> None:
        residual[start][end] = capacity
        residual[end][start] = 0
        adjacency[start].append(end)
        adjacency[end].append(start)

    ordered_documents = _ordered_document_names(document_names)
    for document_name in ordered_documents:
        add_edge(("document", document_name), sink, config.max_queries_per_document)

    availability: Counter[tuple[str, RetrievalEvidenceType]] = Counter(
        (item.document_name, evidence_type)
        for document_name in ordered_documents
        for item, evidence_type in by_document[document_name]
    )
    edge_capacity: dict[tuple[str, RetrievalEvidenceType], int] = {}
    for evidence_type in _EVIDENCE_ORDER:
        evidence_node = ("evidence", evidence_type.value)
        add_edge(source, evidence_node, config.target_query_counts[evidence_type])
        for document_name in ordered_documents:
            capacity = min(
                availability[(document_name, evidence_type)],
                config.max_queries_per_document,
            )
            edge_capacity[(document_name, evidence_type)] = capacity
            if capacity:
                add_edge(evidence_node, ("document", document_name), capacity)

    while True:
        parents: dict[tuple[str, str], tuple[str, str] | None] = {source: None}
        queue = deque([source])
        while queue and sink not in parents:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor not in parents and residual[node][neighbor] > 0:
                    parents[neighbor] = node
                    queue.append(neighbor)
        if sink not in parents:
            break
        path_capacity = sum(config.target_query_counts.values())
        node = sink
        while node != source:
            parent = parents[node]
            assert parent is not None
            path_capacity = min(path_capacity, residual[parent][node])
            node = parent
        node = sink
        while node != source:
            parent = parents[node]
            assert parent is not None
            residual[parent][node] -= path_capacity
            residual[node][parent] += path_capacity
            node = parent

    return {
        (document_name, evidence_type): edge_capacity[(document_name, evidence_type)]
        - residual[("evidence", evidence_type.value)].get(("document", document_name), 0)
        for document_name in ordered_documents
        for evidence_type in _EVIDENCE_ORDER
    }


def _selection_objective(
    *,
    by_document: dict[str, list[tuple[OHRSourceQuestion, RetrievalEvidenceType]]],
    domains: dict[str, str],
    document_names: tuple[str, ...],
    config: OHRSelectionConfig,
) -> tuple[int, int, int, int, str]:
    allocations = _allocate_query_counts(by_document, document_names, config)
    actual = Counter[RetrievalEvidenceType]()
    for (_, evidence_type), count in allocations.items():
        actual[evidence_type] += count
    total_shortfall = sum(
        max(0, config.target_query_counts[evidence_type] - actual[evidence_type])
        for evidence_type in _EVIDENCE_ORDER
    )
    domain_counts = Counter(domains[name] for name in document_names)
    max_domain_count = max(domain_counts.values())
    concentration = sum(count * count for count in domain_counts.values())
    ordered_names = _ordered_document_names(document_names)
    return (
        total_shortfall,
        -len(domain_counts),
        max_domain_count,
        concentration,
        _stable_key(*ordered_names),
    )


def _select_documents(
    eligible: tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...],
    config: OHRSelectionConfig,
) -> tuple[str, ...]:
    by_document, domains = _document_catalog(eligible)

    remaining = set(by_document)
    selected: list[str] = []
    while remaining and len(selected) < config.max_documents:
        document_name = min(
            remaining,
            key=lambda name: _selection_objective(
                by_document=by_document,
                domains=domains,
                document_names=tuple((*selected, name)),
                config=config,
            ),
        )
        selected.append(document_name)
        remaining.remove(document_name)

    while selected and remaining:
        current = tuple(selected)
        current_objective = _selection_objective(
            by_document=by_document,
            domains=domains,
            document_names=current,
            config=config,
        )
        best_selection = current
        best_objective = current_objective
        for removed in _ordered_document_names(current):
            retained = tuple(name for name in current if name != removed)
            for added in _ordered_document_names(remaining):
                candidate = tuple((*retained, added))
                objective = _selection_objective(
                    by_document=by_document,
                    domains=domains,
                    document_names=candidate,
                    config=config,
                )
                if objective < best_objective:
                    best_selection = candidate
                    best_objective = objective
        if best_objective >= current_objective:
            break
        selected = list(best_selection)
        remaining = set(by_document).difference(selected)
    return _ordered_document_names(tuple(selected))


def _select_queries(
    eligible: tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...],
    selected_documents: tuple[str, ...],
    config: OHRSelectionConfig,
) -> tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...]:
    by_document, _ = _document_catalog(eligible)
    candidates: dict[tuple[str, RetrievalEvidenceType], list[OHRSourceQuestion]] = defaultdict(list)
    for document_name in selected_documents:
        for item, evidence_type in by_document[document_name]:
            candidates[(document_name, evidence_type)].append(item)
    for items in candidates.values():
        items.sort(key=lambda item: _stable_key(item.source_dataset_item_id))

    allocations = _allocate_query_counts(by_document, selected_documents, config)
    return tuple(
        (item, evidence_type)
        for evidence_type in _EVIDENCE_ORDER
        for document_name in selected_documents
        for item in candidates[(document_name, evidence_type)][
            : allocations[(document_name, evidence_type)]
        ]
    )


def _selection_digest(
    *,
    source_dataset_revision: str | None,
    source_dataset_digest: Sha256Digest,
    config: OHRSelectionConfig,
    selected_documents: tuple[str, ...],
    selected_items: tuple[tuple[OHRSourceQuestion, RetrievalEvidenceType], ...],
) -> Sha256Digest:
    semantic_payload = {
        "benchmark_id": OHR_BENCHMARK_ID,
        "source_dataset": OHR_SOURCE_DATASET,
        "source_dataset_revision": source_dataset_revision,
        "source_dataset_digest": source_dataset_digest,
        "selection_policy_version": OHR_SELECTION_POLICY_VERSION,
        "selection_config": config.model_dump(mode="json"),
        "selected_document_names": list(selected_documents),
        "selected_query_source_ids": [
            item.source_dataset_item_id for item, _ in selected_items
        ],
    }
    encoded = json.dumps(
        semantic_payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def prepare_ohr_rag_core(
    *,
    dataset_root: Path,
    config: OHRSelectionConfig | None = None,
    source_dataset_revision: str | None = None,
    source_commit: str | None = None,
    created_at: UtcTimestamp | None = None,
) -> PreparedOHRSubset:
    """Select the frozen OHR retrieval subset from locally provisioned metadata."""

    selection_config = config or OHRSelectionConfig()
    questions = load_ohr_questions(dataset_root)
    eligible, excluded = _eligible_items(questions)
    selected_documents = _select_documents(eligible, selection_config)
    selected_items = _select_queries(eligible, selected_documents, selection_config)
    source_digest = _source_dataset_digest(questions)

    queries = tuple(
        RetrievalGroundTruth(
            benchmark_query_id=_query_id(item),
            source_dataset=OHR_SOURCE_DATASET,
            source_dataset_item_id=item.source_dataset_item_id,
            document_name=item.document_name,
            question=item.question,
            answer=item.answer,
            evidence_type=evidence_type,
            evidence_contexts=_normalized_contexts(item.evidence_contexts),
            evidence_page_indices=_normalized_page_indices(item.evidence_page_indices),
            document_type_or_domain=item.document_type_or_domain,
        )
        for item, evidence_type in selected_items
    )

    query_counts = Counter(query.evidence_type for query in queries)
    shortfalls = tuple(
        EvidenceShortfall(
            evidence_type=evidence_type,
            requested=selection_config.target_query_counts.get(evidence_type, 0),
            actual=query_counts[evidence_type],
            reason=(
                "insufficient eligible queries within the selected documents and per-document cap"
            ),
        )
        for evidence_type in _EVIDENCE_ORDER
        if query_counts[evidence_type]
        < selection_config.target_query_counts.get(evidence_type, 0)
    )
    document_domains = {
        document_name: next(
            item.document_type_or_domain
            for item, _ in selected_items
            if item.document_name == document_name
        )
        for document_name in selected_documents
        if any(item.document_name == document_name for item, _ in selected_items)
    }
    selected_documents_with_queries = tuple(
        document_name for document_name in selected_documents if document_name in document_domains
    )
    required_documents = tuple(
        RequiredDocument(
            document_name=document_name,
            source_dataset=OHR_SOURCE_DATASET,
            source_dataset_identity=document_name,
            source_dataset_item_ids=tuple(
                query.source_dataset_item_id
                for query in queries
                if query.document_name == document_name
            ),
        )
        for document_name in selected_documents_with_queries
    )
    manifest = OHRSubsetManifest(
        benchmark_id=OHR_BENCHMARK_ID,
        source_dataset=OHR_SOURCE_DATASET,
        source_dataset_revision=source_dataset_revision,
        source_dataset_digest=source_digest,
        selection_policy_version=OHR_SELECTION_POLICY_VERSION,
        selection_config=selection_config,
        selected_document_count=len(selected_documents_with_queries),
        selected_query_count=len(queries),
        query_count_by_evidence_type={
            evidence_type: query_counts[evidence_type] for evidence_type in _EVIDENCE_ORDER
        },
        document_count_by_domain=dict(sorted(Counter(document_domains.values()).items())),
        selected_document_names=selected_documents_with_queries,
        excluded_query_count_by_evidence_source=excluded,
        shortfalls=shortfalls,
        selection_digest=_selection_digest(
            source_dataset_revision=source_dataset_revision,
            source_dataset_digest=source_digest,
            config=selection_config,
            selected_documents=selected_documents_with_queries,
            selected_items=selected_items,
        ),
        source_commit=source_commit,
        created_at=created_at
        or UtcTimestamp(datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")),
    )
    return PreparedOHRSubset(
        queries=queries,
        manifest=manifest,
        required_documents=required_documents,
    )


def write_ohr_subset(subset: PreparedOHRSubset, output_dir: Path) -> None:
    """Write the prepared artifacts only to the caller-selected external directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    query_lines = [
        json.dumps(
            query.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for query in subset.queries
    ]
    (output_dir / "queries.jsonl").write_text(
        "\n".join(query_lines) + ("\n" if query_lines else ""),
        encoding="utf-8",
    )
    for filename, payload in (
        ("manifest.json", subset.manifest.model_dump(mode="json")),
        (
            "required_documents.json",
            [document.model_dump(mode="json") for document in subset.required_documents],
        ),
    ):
        (output_dir / filename).write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

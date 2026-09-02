"""Deterministic local selection of development and protected ParseBench IDs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from docparser.evaluation.parsebench.models import (
    PARSEBENCH_DATASET_REVISION,
    ParseBenchCandidate,
    ParseBenchStratum,
    ParseBenchSubsetManifest,
    SubsetSelectionStatus,
)
from docparser.ir.types import Sha256Digest

DEVELOPMENT_SEED = 260_401
HOLDOUT_SEED = 260_402
DEVELOPMENT_TARGET = 60
HOLDOUT_TARGET = 20
_STRATUM_ORDER = tuple(ParseBenchStratum)


def _ordering_key(candidate: ParseBenchCandidate, seed: int) -> tuple[str, str]:
    digest = hashlib.sha256(f"{seed}:{candidate.item_id}".encode()).hexdigest()
    return digest, str(candidate.item_id)


def _items_digest(items: Iterable[ParseBenchCandidate]) -> Sha256Digest:
    payload = [item.model_dump(mode="json") for item in items]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def unprovisioned_subset_manifest(
    *,
    dataset_id: str,
    split: Literal["DEVELOPMENT", "PROTECTED_HOLDOUT"],
    seed: int,
    target_count: int,
    access_policy: str,
) -> ParseBenchSubsetManifest:
    return ParseBenchSubsetManifest(
        dataset_id=dataset_id,
        split=split,
        selection_status=SubsetSelectionStatus.UNPROVISIONED,
        seed=seed,
        target_count=target_count,
        selected_items=(),
        selected_item_digest=_items_digest(()),
        access_policy=access_policy,
    )


def _select(
    candidates: tuple[ParseBenchCandidate, ...],
    *,
    count: int,
    seed: int,
    excluded_documents: set[str],
) -> tuple[ParseBenchCandidate, ...]:
    unique: dict[str, ParseBenchCandidate] = {}
    seen_pages: set[tuple[str, int]] = set()
    for candidate in candidates:
        page = (str(candidate.source_document_id), candidate.page_number)
        if str(candidate.item_id) in unique or page in seen_pages:
            raise ValueError("candidate catalog contains duplicate item IDs or source pages")
        unique[str(candidate.item_id)] = candidate
        seen_pages.add(page)
    ordered = sorted(unique.values(), key=lambda item: _ordering_key(item, seed))
    selected: list[ParseBenchCandidate] = []
    selected_documents = set(excluded_documents)
    while len(selected) < count:
        progress = False
        for stratum in _STRATUM_ORDER:
            chosen: ParseBenchCandidate | None = None
            for item in ordered:
                if (
                    item not in selected
                    and str(item.source_document_id) not in selected_documents
                    and stratum in item.strata
                ):
                    chosen = item
                    break
            if chosen is None:
                continue
            selected.append(chosen)
            selected_documents.add(str(chosen.source_document_id))
            progress = True
            if len(selected) >= count:
                break
        if not progress:
            break
    for candidate in ordered:
        if len(selected) >= count:
            break
        document_id = str(candidate.source_document_id)
        if candidate not in selected and document_id not in selected_documents:
            selected.append(candidate)
            selected_documents.add(document_id)
    if len(selected) != count:
        raise ValueError(
            f"candidate catalog cannot provide {count} unique document-family pages; "
            f"got {len(selected)}"
        )
    return tuple(sorted(selected, key=lambda item: _ordering_key(item, seed)))


def prepare_subset_manifests(
    candidates: tuple[ParseBenchCandidate, ...],
) -> tuple[ParseBenchSubsetManifest, ParseBenchSubsetManifest]:
    development_items = _select(
        candidates,
        count=DEVELOPMENT_TARGET,
        seed=DEVELOPMENT_SEED,
        excluded_documents=set(),
    )
    development_documents = {
        str(candidate.source_document_id) for candidate in development_items
    }
    holdout_items = _select(
        candidates,
        count=HOLDOUT_TARGET,
        seed=HOLDOUT_SEED,
        excluded_documents=development_documents,
    )
    development = ParseBenchSubsetManifest(
        dataset_id="parsebench-complex-v1-dev",
        split="DEVELOPMENT",
        selection_status=SubsetSelectionStatus.FROZEN,
        upstream_revision=PARSEBENCH_DATASET_REVISION,
        seed=DEVELOPMENT_SEED,
        target_count=DEVELOPMENT_TARGET,
        selected_items=development_items,
        selected_item_digest=_items_digest(development_items),
        access_policy="development: results visible; parser configuration may be tuned",
    )
    holdout = ParseBenchSubsetManifest(
        dataset_id="parsebench-complex-v1-holdout",
        split="PROTECTED_HOLDOUT",
        selection_status=SubsetSelectionStatus.FROZEN,
        upstream_revision=PARSEBENCH_DATASET_REVISION,
        seed=HOLDOUT_SEED,
        target_count=HOLDOUT_TARGET,
        selected_items=holdout_items,
        selected_item_digest=_items_digest(holdout_items),
        access_policy=(
            "engineering holdout: no parser or Quality Gate tuning; not statistically adequate "
            "for a final 95% claim without a documented confidence analysis"
        ),
    )
    return development, holdout


def load_candidate_catalog(path: Path) -> tuple[ParseBenchCandidate, ...]:
    candidates: list[ParseBenchCandidate] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidates.append(ParseBenchCandidate.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid ParseBench candidate at line {line_number}: {exc}") from exc
    return tuple(candidates)


def write_subset_manifest(manifest: ParseBenchSubsetManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
    path.write_text(payload + "\n", encoding="utf-8")

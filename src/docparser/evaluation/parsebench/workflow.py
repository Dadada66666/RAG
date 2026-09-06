"""Prepare Canonical IR predictions for the pinned official ParseBench evaluator."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
    write_parse_outputs,
)
from docparser.evaluation.parsebench.export import (
    export_document_to_parsebench,
    write_parsebench_prediction,
)
from docparser.evaluation.parsebench.models import (
    PARSEBENCH_DATASET_REVISION,
    ParseBenchSubsetManifest,
    SubsetSelectionStatus,
)
from docparser.ir.types import Sha256Digest

ParseOne = Callable[[Path, ParsingConfig], ParseOutcome]


def load_subset_manifest(path: Path) -> ParseBenchSubsetManifest:
    return ParseBenchSubsetManifest.model_validate_json(path.read_text(encoding="utf-8"))


def manifest_digest(path: Path) -> Sha256Digest:
    return Sha256Digest(f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}")


def prepare_parsebench_predictions(
    manifest: ParseBenchSubsetManifest,
    *,
    dataset_root: Path,
    export_root: Path,
    cases_root: Path,
    config: ParsingConfig,
    parse_one: ParseOne = parse_document_with_diagnostics,
) -> tuple[Path, ...]:
    """Parse every frozen subset item and write official-compatible result artifacts."""

    if manifest.selection_status is not SubsetSelectionStatus.FROZEN:
        raise ValueError("ParseBench subset manifest is not provisioned and frozen")
    if str(manifest.upstream_revision) != PARSEBENCH_DATASET_REVISION:
        raise ValueError("ParseBench subset does not use the pinned dataset revision")
    predictions: list[Path] = []
    for item in manifest.selected_items:
        source = dataset_root / str(item.source_path)
        outcome = parse_one(source, config)
        write_parse_outputs(outcome, cases_root / str(item.item_id))
        prediction = export_document_to_parsebench(
            outcome.document,
            example_id=str(item.item_id),
            pipeline_name=config.parser,
            source_file_path=str(item.source_path),
            latency_in_ms=round(outcome.diagnostics.elapsed_seconds * 1000),
        )
        predictions.append(write_parsebench_prediction(prediction, export_root))
    return tuple(predictions)

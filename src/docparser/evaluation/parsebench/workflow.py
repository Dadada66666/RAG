"""Prepare Canonical IR predictions for the pinned official ParseBench evaluator."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    build_parser,
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
from docparser.ir.serialization import load_canonical_json
from docparser.ir.types import Sha256Digest
from docparser.ports.parsers import DocumentParser

ParseOne = Callable[[Path, ParsingConfig, DocumentParser], ParseOutcome]
ParserFactory = Callable[[ParsingConfig], DocumentParser]


def _parse_one(path: Path, config: ParsingConfig, parser: DocumentParser) -> ParseOutcome:
    return parse_document_with_diagnostics(path, config, parser=parser)


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
    parse_one: ParseOne = _parse_one,
    parser_factory: ParserFactory = build_parser,
    reuse_saved_ir: bool = False,
) -> tuple[Path, ...]:
    """Parse every frozen subset item and write official-compatible result artifacts."""

    if manifest.selection_status is not SubsetSelectionStatus.FROZEN:
        raise ValueError("ParseBench subset manifest is not provisioned and frozen")
    if str(manifest.upstream_revision) != PARSEBENCH_DATASET_REVISION:
        raise ValueError("ParseBench subset does not use the pinned dataset revision")
    predictions: list[Path] = []
    parser = None if reuse_saved_ir else parser_factory(config)
    for item in manifest.selected_items:
        case_root = cases_root / str(item.item_id)
        if reuse_saved_ir:
            document = load_canonical_json((case_root / "document.ir.json").read_bytes())
            diagnostics = json.loads((case_root / "diagnostics.json").read_text(encoding="utf-8"))
            latency_in_ms = round(float(diagnostics["elapsed_seconds"]) * 1000)
        else:
            assert parser is not None
            source = dataset_root / str(item.source_path)
            outcome = parse_one(source, config, parser)
            write_parse_outputs(outcome, case_root)
            document = outcome.document
            latency_in_ms = round(outcome.diagnostics.elapsed_seconds * 1000)
        prediction = export_document_to_parsebench(
            document,
            example_id=str(item.item_id),
            pipeline_name=config.parser,
            source_file_path=str(item.source_path),
            latency_in_ms=latency_in_ms,
        )
        predictions.append(write_parsebench_prediction(prediction, export_root))
    return tuple(predictions)

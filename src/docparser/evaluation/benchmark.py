"""Small local benchmark runner for the development Golden Dataset."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import yaml

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.domain.parser_contract import RuntimeDevice
from docparser.evaluation.metrics import score_outcome
from docparser.evaluation.models import (
    BenchmarkCaseResult,
    BenchmarkFailure,
    GoldenDatasetManifest,
    ParsingBenchmarkReport,
    SliceComparison,
    SliceParserSummary,
)

ParseFunction = Callable[[Path, ParsingConfig], ParseOutcome]


def _average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _slice_comparisons(
    results: list[BenchmarkCaseResult], parser_profiles: tuple[str, ...]
) -> tuple[SliceComparison, ...]:
    slices = sorted({slice_name for result in results for slice_name in result.slices})
    comparisons: list[SliceComparison] = []
    for slice_name in slices:
        parsers: list[SliceParserSummary] = []
        for parser_profile in parser_profiles:
            cases = [
                result
                for result in results
                if slice_name in result.slices and result.parser_profile == parser_profile
            ]
            if not cases:
                continue
            parsers.append(
                SliceParserSummary(
                    parser_profile=parser_profile,
                    case_count=len(cases),
                    text_edit_similarity=_average(
                        [case.metrics.text_edit_similarity for case in cases]
                    ),
                    reading_order_pair_accuracy=_average(
                        [case.metrics.reading_order_pair_accuracy for case in cases]
                    ),
                    cell_exact_text_accuracy=_average(
                        [case.metrics.cell_exact_text_accuracy for case in cases]
                    ),
                    critical_numeric_exact_accuracy=_average(
                        [case.metrics.critical_numeric_exact_accuracy for case in cases]
                    ),
                    resolvable_block_provenance=sum(
                        case.metrics.resolvable_block_provenance for case in cases
                    )
                    / len(cases),
                    average_elapsed_seconds=sum(
                        case.metrics.elapsed_seconds for case in cases
                    )
                    / len(cases),
                )
            )
        comparisons.append(SliceComparison(slice=slice_name, parsers=tuple(parsers)))
    return tuple(comparisons)


def load_manifest(path: Path) -> GoldenDatasetManifest:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenDatasetManifest.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def run_parsing_benchmark(
    manifest: GoldenDatasetManifest,
    *,
    manifest_dir: Path,
    parser_profiles: tuple[str, ...] = ("docling-standard", "paddleocr-vl-1.6"),
    device: RuntimeDevice = RuntimeDevice.AUTO,
    parse_fn: ParseFunction = parse_document_with_diagnostics,
) -> ParsingBenchmarkReport:
    results: list[BenchmarkCaseResult] = []
    failures: list[BenchmarkFailure] = []
    skipped: list[str] = []
    evaluated_pages = 0
    for document in manifest.documents:
        if not document.enabled:
            continue
        source = (manifest_dir / document.local_path).resolve()
        if not source.is_file():
            skipped.append(str(document.document_id))
            continue
        if _sha256(source) != str(document.source_sha256):
            raise ValueError(f"Golden source digest mismatch: {document.document_id}")
        evaluated_pages += len(document.annotations)
        for parser_profile in parser_profiles:
            try:
                outcome = parse_fn(
                    source,
                    ParsingConfig(parser=parser_profile, device=device),
                )
                metrics = score_outcome(outcome, document.annotations)
                results.append(
                    BenchmarkCaseResult(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        slices=document.slices,
                        metrics=metrics,
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                failures.append(
                    BenchmarkFailure(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        message=str(exc),
                    )
                )
    recommendation = (
        "insufficient evidence"
        if evaluated_pages < manifest.target_page_count_min or failures or skipped
        else "profile-based routing pending slice review"
    )
    return ParsingBenchmarkReport(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.version,
        results=tuple(results),
        failures=tuple(failures),
        skipped_missing_documents=tuple(skipped),
        slice_comparisons=_slice_comparisons(results, parser_profiles),
        recommendation=recommendation,
    )


def write_benchmark_report(report: ParsingBenchmarkReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    (output / "parsing-benchmark.json").write_text(payload, encoding="utf-8")
    lines = [
        f"# Parsing benchmark: {report.dataset_id} {report.dataset_version}",
        "",
        "No global parser score is calculated. Metrics remain independent by quality axis.",
        "",
        "| Document | Parser | Text | Table cells | Reading order | Numerics | "
        "Provenance | Seconds |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    def display(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    for result in report.results:
        metric = result.metrics
        lines.append(
            f"| {result.document_id} | {result.parser_profile} | "
            f"{display(metric.text_edit_similarity)} | "
            f"{display(metric.cell_exact_text_accuracy)} | "
            f"{display(metric.reading_order_pair_accuracy)} | "
            f"{display(metric.critical_numeric_exact_accuracy)} | "
            f"{metric.resolvable_block_provenance:.4f} | {metric.elapsed_seconds:.3f} |"
        )
    lines.extend(["", f"Recommendation: **{report.recommendation}**", ""])
    lines.extend(
        [
            "## Comparison by protected slice",
            "",
            "| Slice | Parser | Text | Table cells | Reading order | Numerics | "
            "Provenance | Seconds |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for comparison in report.slice_comparisons:
        for parser in comparison.parsers:
            lines.append(
                f"| {comparison.slice.value} | {parser.parser_profile} | "
                f"{display(parser.text_edit_similarity)} | "
                f"{display(parser.cell_exact_text_accuracy)} | "
                f"{display(parser.reading_order_pair_accuracy)} | "
                f"{display(parser.critical_numeric_exact_accuracy)} | "
                f"{parser.resolvable_block_provenance:.4f} | "
                f"{parser.average_elapsed_seconds:.3f} |"
            )
    lines.append("")
    (output / "parsing-benchmark.md").write_text("\n".join(lines), encoding="utf-8")

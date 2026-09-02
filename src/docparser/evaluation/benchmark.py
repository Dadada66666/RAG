"""Local, offline project benchmark runner with explicit failed-case accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml

from docparser.application.parsing import (
    ParseOutcome,
    ParsingConfig,
    parse_document_with_diagnostics,
)
from docparser.domain.parser_contract import RuntimeDevice
from docparser.evaluation.metrics import (
    PROJECT_METRIC_IMPLEMENTATION_VERSION,
    TEXT_ASSEMBLY_PROFILE,
    evaluation_denominators,
    score_outcome,
)
from docparser.evaluation.models import (
    BenchmarkCaseResult,
    BenchmarkExecutionMetadata,
    BenchmarkFailure,
    BenchmarkOutputStatus,
    DatasetMetricFamily,
    GoldenDatasetManifest,
    MetricStatus,
    ParsingBenchmarkReport,
    SliceComparison,
    SliceParserSummary,
)
from docparser.ir.models import DocumentIR
from docparser.ir.types import Sha256Digest

ParseFunction = Callable[[Path, ParsingConfig], ParseOutcome]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize_cases(
    cases: Sequence[BenchmarkCaseResult], parser_profile: str
) -> SliceParserSummary:
    """Aggregate explicit document/page and micro denominators for one parser slice."""
    case_count = len(cases)
    if case_count == 0:
        raise ValueError("slice parser summary requires at least one case")
    successful = [case for case in cases if case.metrics is not None]
    failed = [case for case in cases if case.metrics is None]
    incomplete = [
        case for case in cases if case.output_status is BenchmarkOutputStatus.METRIC_INCOMPLETE
    ]
    pages = sum(case.denominators.pages for case in cases)
    tables = sum(case.denominators.tables for case in cases)
    cells = sum(case.denominators.cells for case in cases)
    numerics = sum(case.denominators.numeric_annotations for case in cases)
    page_macro = (
        sum(case.metrics.page_completeness if case.metrics is not None else 0.0 for case in cases)
        / case_count
    )
    pages_present = sum(
        case.metrics.pages_present for case in successful if case.metrics is not None
    )

    text_applicable = [case for case in cases if case.denominators.text_pages > 0]
    text_incomplete = any(
        case.metrics is not None and case.metrics.text_metric_status is MetricStatus.INCOMPLETE
        for case in text_applicable
    )
    text_macro: float | None
    if not text_applicable or text_incomplete:
        text_macro = None
    else:
        text_macro = sum(
            case.metrics.text_edit_similarity
            if case.metrics is not None and case.metrics.text_edit_similarity is not None
            else 0.0
            for case in text_applicable
        ) / len(text_applicable)
    text_pages_scored = sum(
        case.metrics.text_pages_scored for case in text_applicable if case.metrics is not None
    )
    text_page_macro = (
        None
        if not text_applicable or text_incomplete or text_pages_scored == 0
        else sum(
            (case.metrics.text_edit_similarity or 0.0) * case.metrics.text_pages_scored
            for case in text_applicable
            if case.metrics is not None
        )
        / text_pages_scored
    )

    reading_correct = sum(
        case.metrics.reading_order_pairs_correct for case in successful if case.metrics is not None
    )
    reading_expected = sum(case.denominators.reading_order_pairs for case in cases)
    detection_tp = sum(
        case.metrics.table_detection_tp for case in successful if case.metrics is not None
    )
    detection_fp = sum(
        case.metrics.table_detection_fp for case in successful if case.metrics is not None
    )
    cells_correct = sum(
        case.metrics.cells_text_correct for case in successful if case.metrics is not None
    )
    page_numeric_correct = sum(
        case.metrics.page_numeric_presence_correct
        for case in successful
        if case.metrics is not None
    )
    structural_numeric_correct = sum(
        case.metrics.structural_numerics_correct for case in successful if case.metrics is not None
    )
    structural_numeric_expected = sum(
        case.denominators.structural_numeric_annotations for case in cases
    )
    return SliceParserSummary(
        parser_profile=parser_profile,
        case_count=case_count,
        documents=case_count,
        pages=pages,
        tables=tables,
        cells=cells,
        numeric_annotations=numerics,
        successful_outputs=len(successful),
        failed_outputs=len(failed),
        metric_incomplete_outputs=len(incomplete),
        output_coverage=len(successful) / case_count,
        page_completeness_document_macro=page_macro,
        page_completeness_page_macro=pages_present / pages if pages else 0.0,
        text_edit_similarity_document_macro=text_macro,
        text_edit_similarity_page_macro=text_page_macro,
        reading_order_pair_accuracy_micro=_ratio(reading_correct, reading_expected),
        table_detection_precision_micro=_ratio(detection_tp, detection_tp + detection_fp),
        table_detection_recall_micro=_ratio(detection_tp, tables),
        cell_exact_text_accuracy_micro=_ratio(cells_correct, cells),
        page_numeric_presence_accuracy_micro=_ratio(page_numeric_correct, numerics),
        critical_numeric_structural_exact_accuracy_micro=_ratio(
            structural_numeric_correct, structural_numeric_expected
        ),
        resolvable_block_provenance_document_macro=sum(
            case.metrics.resolvable_block_provenance if case.metrics is not None else 0.0
            for case in cases
        )
        / case_count,
        average_elapsed_seconds=(
            sum(case.metrics.elapsed_seconds for case in successful if case.metrics is not None)
            / len(successful)
            if successful
            else 0.0
        ),
    )


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
            if cases:
                parsers.append(summarize_cases(cases, parser_profile))
        comparisons.append(SliceComparison(slice=slice_name, parsers=tuple(parsers)))
    return tuple(comparisons)


def load_manifest(path: Path) -> GoldenDatasetManifest:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenDatasetManifest.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_digest(manifest: GoldenDatasetManifest) -> Sha256Digest:
    payload = manifest.model_dump(mode="json", exclude={"manifest_digest"})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    computed = Sha256Digest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")
    if manifest.manifest_digest is not None and manifest.manifest_digest != computed:
        raise ValueError("declared Golden manifest digest does not match its content")
    return computed


def _execution_metadata(document: DocumentIR) -> BenchmarkExecutionMetadata | None:
    if not document.processing.parser_runs:
        return None
    parser_run = document.processing.parser_runs[0]
    return BenchmarkExecutionMetadata(
        parser_name=parser_run.parser_name,
        parser_version=parser_run.parser_version,
        adapter_version=parser_run.adapter_version,
        model_identifiers=tuple(str(value) for value in parser_run.model_ids),
        pipeline_version=document.processing.pipeline_version,
        normalizer_version=document.processing.normalizer_version,
        config_digest=document.processing.config_hash,
    )


def _metric_family_label(manifest: GoldenDatasetManifest) -> str:
    if manifest.metric_family is DatasetMetricFamily.PARSEBENCH_DERIVED_PROJECT:
        return f"Project metrics on ParseBench-derived {manifest.dataset_id}"
    return "Project Golden Dataset metrics"


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
    evaluated_pages = 0
    for document in manifest.documents:
        if not document.enabled:
            continue
        denominators = evaluation_denominators(document.annotations)
        source = (manifest_dir / document.local_path).resolve()
        source_error: str | None = None
        if not source.is_file():
            source_error = "Golden source is missing"
        elif _sha256(source) != str(document.source_sha256):
            source_error = "Golden source digest mismatch"
        if source_error is not None:
            for parser_profile in parser_profiles:
                results.append(
                    BenchmarkCaseResult(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        slices=document.slices,
                        output_status=BenchmarkOutputStatus.MISSING_INPUT,
                        denominators=denominators,
                        metrics=None,
                        error_message=source_error,
                    )
                )
                failures.append(
                    BenchmarkFailure(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        output_status=BenchmarkOutputStatus.MISSING_INPUT,
                        message=source_error,
                    )
                )
            continue
        evaluated_pages += len(document.annotations)
        for parser_profile in parser_profiles:
            try:
                outcome = parse_fn(source, ParsingConfig(parser=parser_profile, device=device))
                metrics = score_outcome(outcome, document.annotations)
                status = (
                    BenchmarkOutputStatus.METRIC_INCOMPLETE
                    if metrics.text_metric_status is MetricStatus.INCOMPLETE
                    else BenchmarkOutputStatus.SUCCESS
                )
                message = (
                    metrics.text_incomplete_reason
                    if status is BenchmarkOutputStatus.METRIC_INCOMPLETE
                    else None
                )
                results.append(
                    BenchmarkCaseResult(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        slices=document.slices,
                        output_status=status,
                        denominators=denominators,
                        execution=_execution_metadata(outcome.document),
                        metrics=metrics,
                        error_message=message,
                    )
                )
                if status is BenchmarkOutputStatus.METRIC_INCOMPLETE:
                    failures.append(
                        BenchmarkFailure(
                            document_id=document.document_id,
                            parser_profile=parser_profile,
                            output_status=status,
                            message=message or "mandatory metric incomplete",
                        )
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                message = str(exc) or type(exc).__name__
                results.append(
                    BenchmarkCaseResult(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        slices=document.slices,
                        output_status=BenchmarkOutputStatus.PARSER_FAILED,
                        denominators=denominators,
                        metrics=None,
                        error_message=message,
                    )
                )
                failures.append(
                    BenchmarkFailure(
                        document_id=document.document_id,
                        parser_profile=parser_profile,
                        output_status=BenchmarkOutputStatus.PARSER_FAILED,
                        message=message,
                    )
                )
    complete = (
        bool(manifest.documents)
        and not failures
        and (evaluated_pages >= manifest.target_page_count_min)
    )
    accuracy_claim_status = (
        f"{_metric_family_label(manifest)} — development evidence only"
        if complete
        else "NO ACCURACY CLAIM — benchmark corpus/runtime unavailable"
    )
    recommendation = (
        "profile-based routing pending slice review" if complete else "insufficient evidence"
    )
    return ParsingBenchmarkReport(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.version,
        metric_family=manifest.metric_family,
        split=manifest.split,
        manifest_digest=_manifest_digest(manifest),
        metric_implementation_version=PROJECT_METRIC_IMPLEMENTATION_VERSION,
        text_assembly_profile=TEXT_ASSEMBLY_PROFILE,
        results=tuple(results),
        failures=tuple(failures),
        slice_comparisons=_slice_comparisons(results, parser_profiles),
        benchmark_complete=complete,
        recommendation=recommendation,
        accuracy_claim_status=accuracy_claim_status,
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
    if report.metric_family is DatasetMetricFamily.PARSEBENCH_DERIVED_PROJECT:
        family = f"Project metrics on ParseBench-derived {report.dataset_id}"
    else:
        family = "Project Golden Dataset metrics"
    lines = [
        f"# {family}: {report.dataset_id} {report.dataset_version}",
        "",
        "These are project-local metrics. They are not Official ParseBench metrics or scores.",
        "No global parser score is calculated.",
        "",
        f"Manifest digest: `{report.manifest_digest}`",
        f"Metric implementation: `{report.metric_implementation_version}`",
        f"Text assembly profile: `{report.text_assembly_profile}`",
        "",
        f"Benchmark complete: **{str(report.benchmark_complete).lower()}**",
        f"Accuracy claim status: **{report.accuracy_claim_status}**",
        "",
        "| Document | Parser | Status | Pages | Tables | Cells | Numerics | Text | "
        "Table cells | Structural numerics | Seconds |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def display(value: float | None) -> str:
        return "N/A" if value is None else f"{value:.4f}"

    for result in report.results:
        metric = result.metrics
        lines.append(
            f"| {result.document_id} | {result.parser_profile} | {result.output_status.value} | "
            f"{result.denominators.pages} | {result.denominators.tables} | "
            f"{result.denominators.cells} | {result.denominators.numeric_annotations} | "
            f"{display(metric.text_edit_similarity if metric else None)} | "
            f"{display(metric.cell_exact_text_accuracy if metric else None)} | "
            f"{display(metric.critical_numeric_structural_exact_accuracy if metric else None)} | "
            f"{metric.elapsed_seconds:.3f} |"
            if metric is not None
            else (
                f"| {result.document_id} | {result.parser_profile} | "
                f"{result.output_status.value} | "
                f"{result.denominators.pages} | {result.denominators.tables} | "
                f"{result.denominators.cells} | "
                f"{result.denominators.numeric_annotations} | "
                "N/A | N/A | N/A | N/A |"
            )
        )
    lines.extend(["", f"Recommendation: **{report.recommendation}**", ""])
    lines.extend(
        [
            "## Comparison by declared slice",
            "",
            "| Slice | Parser | Cases | Success | Failed | Incomplete | Pages | Tables | "
            "Cells | Numerics | Output coverage | Page macro | Cell micro | "
            "Numeric structural micro |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for comparison in report.slice_comparisons:
        for parser in comparison.parsers:
            lines.append(
                f"| {comparison.slice.value} | {parser.parser_profile} | {parser.case_count} | "
                f"{parser.successful_outputs} | {parser.failed_outputs} | "
                f"{parser.metric_incomplete_outputs} | {parser.pages} | {parser.tables} | "
                f"{parser.cells} | {parser.numeric_annotations} | {parser.output_coverage:.4f} | "
                f"{parser.page_completeness_document_macro:.4f} | "
                f"{display(parser.cell_exact_text_accuracy_micro)} | "
                f"{display(parser.critical_numeric_structural_exact_accuracy_micro)} |"
            )
    lines.append("")
    (output / "parsing-benchmark.md").write_text("\n".join(lines), encoding="utf-8")

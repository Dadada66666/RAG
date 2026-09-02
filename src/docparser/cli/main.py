"""Project command-line interface."""

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from docparser.application.parsing import (
    ParsingConfig,
    parse_document_with_diagnostics,
    write_parse_outputs,
)
from docparser.application.robust import robust_parse_document, write_robust_outputs
from docparser.config import load_config
from docparser.domain.parser_contract import RuntimeDevice
from docparser.evaluation import load_manifest, run_parsing_benchmark, write_benchmark_report
from docparser.evaluation.parsebench.subset import (
    load_candidate_catalog,
    prepare_subset_manifests,
    write_subset_manifest,
)
from docparser.evaluation.schema import (
    DEFAULT_EVALUATION_SCHEMA,
    evaluation_schema_is_current,
    parsebench_subset_schema_is_current,
    write_evaluation_schema,
    write_parsebench_subset_schema,
)
from docparser.fallback import FallbackProfile
from docparser.ir.schema import (
    DEFAULT_SCHEMA_PATH,
    schema_is_current,
    write_document_ir_schema,
)
from docparser.quality import CalibrationProfile
from docparser.version import __version__

app = typer.Typer(
    name="docparser",
    help="Enterprise document parsing and RAG ingestion platform.",
    no_args_is_help=True,
)
schema_app = typer.Typer(help="Generate and verify committed wire schemas.")
app.add_typer(schema_app, name="schema")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run document parsing platform commands."""


@app.command()
def doctor(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Bootstrap YAML configuration file.",
        ),
    ],
) -> None:
    """Validate bootstrap configuration without external side effects."""

    try:
        settings = load_config(config)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"configuration invalid: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        "configuration valid "
        f"(pipeline={settings.pipeline.version}, storage={settings.storage.backend})"
    )


@app.command("parse-local")
def parse_local(
    input_pdf: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local PDF to parse.",
        ),
    ],
    parser: Annotated[str, typer.Option("--parser", help="Parser profile under evaluation.")] = (
        "docling-standard"
    ),
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./output"),
) -> None:
    """Parse a local PDF through the Phase 2.6 development/evaluation slice."""

    try:
        outcome = parse_document_with_diagnostics(
            input_pdf,
            ParsingConfig(parser=parser, device=device),
            raw_output_dir=output / "raw",
        )
        write_parse_outputs(outcome, output)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"parse failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"parsed {outcome.diagnostics.pages_parsed}/{outcome.diagnostics.pages_requested} "
        f"pages with {outcome.parse_result.descriptor.parser_name} "
        f"on {outcome.diagnostics.device.value}; output={output}"
    )


@app.command("parse-robust")
def parse_robust(
    input_pdf: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local PDF to parse through the calibrated risk gate.",
        ),
    ],
    parser: Annotated[
        str,
        typer.Option("--parser", help="Primary parser profile."),
    ] = "docling-standard",
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./robust-output"),
    calibration_profile: Annotated[
        Path | None,
        typer.Option(
            "--calibration-profile",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    fallback_profile: Annotated[
        Path | None,
        typer.Option(
            "--fallback-profile",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    supported_slice: Annotated[
        str | None,
        typer.Option(
            "--supported-slice",
            help="Calibrated document slice to evaluate; independent of fallback configuration.",
        ),
    ] = None,
) -> None:
    """Parse, validate, optionally fall back, and emit the final evaluated IR."""

    try:
        calibration = (
            CalibrationProfile.model_validate_json(calibration_profile.read_bytes())
            if calibration_profile
            else None
        )
        fallback = (
            FallbackProfile.model_validate_json(fallback_profile.read_bytes())
            if fallback_profile
            else None
        )
        outcome = robust_parse_document(
            input_pdf,
            ParsingConfig(parser=parser, device=device),
            calibration=calibration,
            fallback_profile=fallback,
            supported_slice=supported_slice,
        )
        write_robust_outputs(outcome, output)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        typer.echo(f"robust parse failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    mode = outcome.final_quality_report.mode.value
    suffix = " / CALIBRATION_REQUIRED" if outcome.final_quality_report.calibration_required else ""
    typer.echo(
        f"robust parse decision={outcome.final_decision.value} mode={mode}{suffix}; output={output}"
    )


@app.command("benchmark-parsing")
def benchmark_parsing(
    manifest: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./benchmark-output"),
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
) -> None:
    """Compare Docling and PaddleOCR-VL on the same local Golden manifest."""

    try:
        dataset = load_manifest(manifest)
        report = run_parsing_benchmark(
            dataset,
            manifest_dir=manifest.parent,
            device=device,
        )
        write_benchmark_report(report, output)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        typer.echo(f"benchmark failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"benchmark cases={len(report.results)} failures={len(report.failures)} "
        f"recommendation={report.recommendation}; output={output}"
    )


@app.command("prepare-parsebench-manifests")
def prepare_parsebench_manifests(
    candidate_catalog: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local JSONL metadata catalog; no PDF data is downloaded.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./tests/golden/manifests"),
) -> None:
    """Freeze deterministic development/holdout IDs from a local candidate catalog."""

    try:
        development, holdout = prepare_subset_manifests(load_candidate_catalog(candidate_catalog))
        write_subset_manifest(development, output / "parsebench-complex-v1-dev.json")
        write_subset_manifest(holdout, output / "parsebench-complex-v1-holdout.json")
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"ParseBench manifest preparation failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"prepared {len(development.selected_items)} development and "
        f"{len(holdout.selected_items)} protected-holdout IDs"
    )


@schema_app.command("generate")
def schema_generate(
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Generated schema output path."),
    ] = DEFAULT_SCHEMA_PATH,
) -> None:
    """Generate the committed Document IR schema from Pydantic models."""

    write_document_ir_schema(output)
    if output == DEFAULT_SCHEMA_PATH:
        write_evaluation_schema()
        write_parsebench_subset_schema()
    typer.echo(f"generated {output.as_posix()}")


@schema_app.command("check")
def schema_check(
    schema_path: Annotated[
        Path,
        typer.Option("--schema", dir_okay=False, help="Committed schema path."),
    ] = DEFAULT_SCHEMA_PATH,
) -> None:
    """Fail when the committed schema differs from the generated contract."""

    if not schema_is_current(schema_path):
        typer.echo(f"schema drift detected: {schema_path.as_posix()}", err=True)
        raise typer.Exit(code=1)
    if schema_path == DEFAULT_SCHEMA_PATH and not evaluation_schema_is_current():
        typer.echo(f"schema drift detected: {DEFAULT_EVALUATION_SCHEMA.as_posix()}", err=True)
        raise typer.Exit(code=1)
    if schema_path == DEFAULT_SCHEMA_PATH and not parsebench_subset_schema_is_current():
        typer.echo("schema drift detected: ParseBench subset schema", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"schema current: {schema_path.as_posix()}")

"""Project command-line interface."""

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
from docparser.config import load_config
from docparser.domain.parser_contract import RuntimeDevice
from docparser.ir.schema import (
    DEFAULT_SCHEMA_PATH,
    schema_is_current,
    write_document_ir_schema,
)
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
    parser: Annotated[str, typer.Option("--parser", help="Primary parser adapter.")] = "docling",
    device: Annotated[
        RuntimeDevice,
        typer.Option("--device", help="auto, cpu, or cuda."),
    ] = RuntimeDevice.AUTO,
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ] = Path("./output"),
) -> None:
    """Parse a local PDF through the Phase 2.5 development vertical slice."""

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


@schema_app.command("generate")
def schema_generate(
    output: Annotated[
        Path,
        typer.Option("--output", dir_okay=False, help="Generated schema output path."),
    ] = DEFAULT_SCHEMA_PATH,
) -> None:
    """Generate the committed Document IR schema from Pydantic models."""

    write_document_ir_schema(output)
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
    typer.echo(f"schema current: {schema_path.as_posix()}")

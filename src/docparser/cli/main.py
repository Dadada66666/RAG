"""Project command-line interface."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from docparser.config import load_config
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

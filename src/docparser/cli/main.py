"""Phase 0 command-line interface."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from docparser.config import load_config
from docparser.version import __version__

app = typer.Typer(
    name="docparser",
    help="Enterprise document parsing and RAG ingestion platform.",
    no_args_is_help=True,
)


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


from pathlib import Path

from typer.testing import CliRunner

from docparser.cli.main import app
from docparser.version import __version__

runner = CliRunner()


def test_help_succeeds() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_version_succeeds() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_doctor_accepts_default_config() -> None:
    result = runner.invoke(app, ["doctor", "--config", "configs/default.yaml"])

    assert result.exit_code == 0
    assert "configuration valid" in result.stdout


def test_doctor_rejects_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("pipeline: {}\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 2
    assert "configuration invalid" in result.stderr


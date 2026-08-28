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


def test_schema_check_accepts_committed_schema() -> None:
    result = runner.invoke(app, ["schema", "check"])

    assert result.exit_code == 0
    assert "schema current" in result.stdout


def test_schema_generate_and_drift_check(tmp_path: Path) -> None:
    schema_path = tmp_path / "document-ir.schema.json"
    generated = runner.invoke(app, ["schema", "generate", "--output", str(schema_path)])
    current = runner.invoke(app, ["schema", "check", "--schema", str(schema_path)])
    schema_path.write_text("{}\n", encoding="utf-8")
    drifted = runner.invoke(app, ["schema", "check", "--schema", str(schema_path)])

    assert generated.exit_code == 0
    assert current.exit_code == 0
    assert drifted.exit_code == 1
    assert "schema drift detected" in drifted.stderr

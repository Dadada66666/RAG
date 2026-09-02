from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.parser_fixture import load_contract_result
from tests.pdf_factory import write_tiny_pdf
from typer.testing import CliRunner

from docparser.application.parsing import ParsingConfig, parse_document_with_diagnostics
from docparser.cli.main import app
from docparser.domain.parser_contract import RuntimeDevice
from docparser.quality import QualityDecision, QualityMode
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


def test_parse_robust_accepts_independent_supported_slice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_robust_parse_document(
        path: Path,
        config: ParsingConfig,
        **kwargs: object,
    ) -> SimpleNamespace:
        captured["supported_slice"] = kwargs["supported_slice"]
        return SimpleNamespace(
            final_quality_report=SimpleNamespace(
                mode=QualityMode.OBSERVE_ONLY,
                calibration_required=True,
            ),
            final_decision=QualityDecision.REJECT,
        )

    monkeypatch.setattr(
        "docparser.cli.main.robust_parse_document",
        fake_robust_parse_document,
    )
    monkeypatch.setattr("docparser.cli.main.write_robust_outputs", lambda *_: None)
    input_pdf = write_tiny_pdf(tmp_path / "robust.pdf")
    result = runner.invoke(
        app,
        [
            "parse-robust",
            str(input_pdf),
            "--supported-slice",
            "enterprise-financial",
            "--output",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0
    assert captured["supported_slice"] == "enterprise-financial"


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


def test_parse_local_writes_development_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.application.test_parsing import ContractFixtureParser

    input_pdf = write_tiny_pdf(tmp_path / "input.pdf")
    outcome = parse_document_with_diagnostics(
        input_pdf,
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=ContractFixtureParser(load_contract_result("born-digital")),
    )
    monkeypatch.setattr(
        "docparser.cli.main.parse_document_with_diagnostics",
        lambda *args, **kwargs: outcome,
    )
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "parse-local",
            str(input_pdf),
            "--parser",
            "docling",
            "--device",
            "cpu",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert "parsed 1/1 pages" in result.stdout
    assert (output / "document.ir.json").is_file()
    assert (output / "parse-result.json").is_file()
    assert (output / "diagnostics.json").is_file()
    assert (output / "raw").is_dir()

from pathlib import Path

import pytest
from pydantic import ValidationError

from docparser.config import load_config


def test_default_config_is_valid() -> None:
    config = load_config(Path("configs/default.yaml"))

    assert config.pipeline.primary_parser == "docling"
    assert config.storage.backend == "local"


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    source = Path("configs/default.yaml").read_text(encoding="utf-8")
    config_path = tmp_path / "unknown-key.yaml"
    config_path.write_text(f"{source}\nunexpected: true\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="unexpected"):
        load_config(config_path)


def test_missing_config_field_is_rejected(tmp_path: Path) -> None:
    source = Path("configs/default.yaml").read_text(encoding="utf-8")
    config_path = tmp_path / "missing-field.yaml"
    config_path.write_text(source.replace('  version: "1.0.0"\n', "", 1), encoding="utf-8")

    with pytest.raises(ValidationError, match="version"):
        load_config(config_path)


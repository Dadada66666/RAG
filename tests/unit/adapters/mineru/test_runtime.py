from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from docparser.adapters.parsers.mineru import MinerUOptions, MinerUParserAdapter
from docparser.adapters.parsers.mineru.runtime import MinerURuntimeError
from docparser.application.parsing import ParsingConfig, build_parser
from docparser.domain.parser_contract import ParseRequest


class _FakeRunner:
    def __init__(self, payload: dict[str, object], *, version: str = "3.4.5") -> None:
        self.payload = payload
        self.version = version
        self.calls: list[tuple[tuple[str, ...], Mapping[str, str]]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check
        self.calls.append((tuple(args), env))
        if list(args)[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, f"mineru {self.version}", "")
        output = Path(list(args)[list(args).index("-o") + 1])
        native = output / "document" / "hybrid_auto"
        native.mkdir(parents=True, exist_ok=True)
        (native / "document_middle.json").write_text(json.dumps(self.payload), encoding="utf-8")
        (native / "document_model.json").write_text("[]", encoding="utf-8")
        (native / "document_content_list.json").write_text("[]", encoding="utf-8")
        (native / "images").mkdir()
        return subprocess.CompletedProcess(args, 0, "", "")


def _payload() -> dict[str, object]:
    value = json.loads(Path("tests/fixtures/mineru/text-figure.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_subprocess_contract_is_exact_and_preserves_native_output(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    runner = _FakeRunner(_payload())
    adapter = MinerUParserAdapter(MinerUOptions(executable="/env/mineru"), runner=runner)

    result = adapter.parse(ParseRequest(source_path=source, raw_output_dir=tmp_path / "raw"))

    parse_args, environment = runner.calls[1]
    assert parse_args == (
        "/env/mineru",
        "-p",
        str(source),
        "-o",
        str(tmp_path / "raw" / "mineru-native"),
        "-b",
        "hybrid-engine",
        "--effort",
        "high",
        "-m",
        "auto",
    )
    assert environment["MINERU_MODEL_SOURCE"] == "local"
    assert result.descriptor.profile == "mineru-3.4.5-hybrid-high-auto"
    assert result.descriptor.adapter_version == "0.1.2"
    middle_path = (
        tmp_path / "raw" / "mineru-native" / "document" / "hybrid_auto" / "document_middle.json"
    )
    assert middle_path.is_file()
    assert middle_path.with_name("document_model.json").is_file()
    assert middle_path.with_name("document_content_list.json").is_file()
    assert middle_path.with_name("images").is_dir()


@pytest.mark.parametrize("profile", ["mineru", "mineru-hybrid-high"])
def test_application_builds_registered_mineru_profiles(
    profile: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCPARSER_MINERU_EXECUTABLE", "/env/mineru")

    parser = build_parser(ParsingConfig(parser=profile))

    assert parser.descriptor().parser_name == "mineru"
    assert parser.descriptor().profile == "mineru-3.4.5-hybrid-high-auto"


def test_wrong_runtime_version_is_parser_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    adapter = MinerUParserAdapter(runner=_FakeRunner(_payload(), version="3.4.4"))

    with pytest.raises(MinerURuntimeError) as caught:
        adapter.parse(ParseRequest(source_path=source))

    assert caught.value.error.code == "PARSER_UNAVAILABLE"


def test_invalid_middle_contract_is_invalid_output(tmp_path: Path) -> None:
    source = tmp_path / "document.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    payload = _payload()
    payload["_effort"] = "medium"
    adapter = MinerUParserAdapter(runner=_FakeRunner(payload))

    with pytest.raises(MinerURuntimeError) as caught:
        adapter.parse(ParseRequest(source_path=source))

    assert caught.value.error.code == "INVALID_OUTPUT"
    assert "effort must be high" in str(caught.value)

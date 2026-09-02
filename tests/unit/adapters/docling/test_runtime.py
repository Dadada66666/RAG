from __future__ import annotations

import json
from pathlib import Path

import pytest

from docparser.adapters.parsers.docling import DoclingOptions, DoclingParserAdapter
from docparser.adapters.parsers.docling.runtime import DoclingRuntimeError, resolve_device
from docparser.domain.parser_contract import (
    ParseRequest,
    ParserHealthStatus,
    ParseScope,
    ParseScopeKind,
    RuntimeDevice,
)


def test_optional_docling_dependency_does_not_break_core_import() -> None:
    health = DoclingParserAdapter(DoclingOptions(device=RuntimeDevice.CPU)).health()

    assert health.status in {ParserHealthStatus.READY, ParserHealthStatus.UNAVAILABLE}


def test_cpu_is_always_a_valid_device_path() -> None:
    assert resolve_device(RuntimeDevice.CPU) is RuntimeDevice.CPU


def test_explicit_cuda_failure_is_not_a_document_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "docparser.adapters.parsers.docling.runtime.cuda_is_available", lambda: False
    )

    with pytest.raises(DoclingRuntimeError, match="explicitly requested") as raised:
        resolve_device(RuntimeDevice.CUDA)

    assert raised.value.error.code == "RUNTIME_UNAVAILABLE"


def test_raw_snapshot_omits_host_paths_and_image_payloads(tmp_path: Path) -> None:
    DoclingParserAdapter._write_raw_snapshot(
        {
            "origin": {"filename": "C:/secret/input.pdf"},
            "body": {"children": []},
            "groups": [],
            "texts": [],
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": {"uri": "C:/secret/image.png", "data": "binary"},
                }
            ],
            "tables": [],
            "pages": {
                "1": {
                    "size": {"width": 100.0, "height": 200.0},
                    "image": {"uri": "C:/secret/page.png"},
                }
            },
        },
        tmp_path,
    )

    snapshot = json.loads((tmp_path / "docling-document.json").read_text(encoding="utf-8"))
    assert "origin" not in snapshot
    assert "image" not in snapshot["pictures"][0]
    assert snapshot["pages"]["1"] == {"size": {"height": 200.0, "width": 100.0}}


def test_adapter_uses_docling_stable_document_export() -> None:
    class FakeDocument:
        def export_to_dict(self) -> dict[str, object]:
            return {"schema_name": "DoclingDocument", "pages": {}}

    class FakeConversion:
        document = FakeDocument()

    assert DoclingParserAdapter._document_payload(FakeConversion()) == {
        "schema_name": "DoclingDocument",
        "pages": {},
    }


def test_non_pdf_input_is_rejected_as_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(DoclingRuntimeError, match="PDF input only") as raised:
        DoclingParserAdapter().parse(ParseRequest(source_path=path))

    assert raised.value.error.code == "UNSUPPORTED_DOCUMENT"


def test_docling_truthfully_rejects_page_scope_until_runtime_supports_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-test")
    adapter = DoclingParserAdapter()

    assert adapter.descriptor().supported_scopes == (ParseScopeKind.DOCUMENT,)
    with pytest.raises(DoclingRuntimeError, match="complete documents only"):
        adapter.parse(
            ParseRequest(
                source_path=path,
                scope=ParseScope(kind=ParseScopeKind.PAGE, page_numbers=(1,)),
            )
        )


def test_parser_exception_is_mapped_at_adapter_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.pdf"
    path.write_bytes(b"%PDF-test")
    adapter = DoclingParserAdapter(DoclingOptions(device=RuntimeDevice.CPU))

    def fail_conversion(_path: Path, _device: RuntimeDevice) -> None:
        raise ValueError("model failure")

    monkeypatch.setattr(
        "docparser.adapters.parsers.docling.adapter.docling_is_compatible", lambda: True
    )
    monkeypatch.setattr(adapter, "_convert", fail_conversion)

    with pytest.raises(DoclingRuntimeError, match="Docling parser failed") as raised:
        adapter.parse(ParseRequest(source_path=path, device=RuntimeDevice.CPU))

    assert raised.value.error.code == "PARSER_FAILURE"
    assert "model failure" not in str(raised.value)

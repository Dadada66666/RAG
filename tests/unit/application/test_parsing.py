from __future__ import annotations

from pathlib import Path

from tests.parser_fixture import load_recorded_result, normalize_recorded
from tests.pdf_factory import write_tiny_pdf

from docparser.application.parsing import (
    ParsingConfig,
    _diagnostics,
    parse_document,
    parse_document_with_diagnostics,
    write_parse_outputs,
)
from docparser.domain.parser_contract import (
    ParserDescriptor,
    ParseRequest,
    ParseResult,
    ParserHealth,
    RuntimeDevice,
)
from docparser.ir.ids import ArtifactId, RevisionId


class RecordedParser:
    def __init__(self, result: ParseResult) -> None:
        self._result = result

    def descriptor(self) -> ParserDescriptor:
        return self._result.descriptor

    def health(self) -> ParserHealth:
        raise AssertionError("health is not needed by the application parse path")

    def parse(self, request: ParseRequest) -> ParseResult:
        return self._result.model_copy(
            update={"pages_requested": request.scope.page_numbers}
        )


def _revision_id() -> RevisionId:
    return RevisionId("rev_018bcfe5-6800-7000-8000-000000000021")


def _artifact_id() -> ArtifactId:
    return ArtifactId("art_018bcfe5-6800-7000-8000-000000000022")


def test_vertical_slice_returns_metric_ready_valid_ir(tmp_path: Path) -> None:
    path = write_tiny_pdf(tmp_path / "input.pdf")
    outcome = parse_document_with_diagnostics(
        path,
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=RecordedParser(load_recorded_result("born-digital")),
        revision_id_factory=_revision_id,
        artifact_id_factory=_artifact_id,
    )

    assert outcome.document.page_count == 1
    assert outcome.diagnostics.ir_validation_passed
    assert (
        outcome.diagnostics.provenance_complete_blocks
        == outcome.diagnostics.generated_blocks
    )
    assert outcome.diagnostics.device is RuntimeDevice.CPU


def test_benchmark_hook_and_output_files_do_not_require_cli_scraping(
    tmp_path: Path,
) -> None:
    path = write_tiny_pdf(tmp_path / "input.pdf")
    parser = RecordedParser(load_recorded_result("born-digital"))
    document = parse_document(
        path,
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=parser,
    )
    outcome = parse_document_with_diagnostics(
        path,
        ParsingConfig(device=RuntimeDevice.CPU),
        parser=parser,
        revision_id_factory=_revision_id,
        artifact_id_factory=_artifact_id,
    )
    output = tmp_path / "out"
    write_parse_outputs(outcome, output)

    assert document.page_count == 1
    assert (output / "document.ir.json").is_file()
    assert (output / "parse-result.json").is_file()
    assert (output / "diagnostics.json").is_file()
    assert (output / "raw").is_dir()


def test_diagnostics_disclose_unmerged_adjacent_table_candidates() -> None:
    result = load_recorded_result("simple-table")
    normalized = normalize_recorded("simple-table")
    first = normalized.tables[0]
    second = first.model_copy(
        update={
            "segments": (
                first.segments[0].model_copy(update={"page_number": 2}),
            )
        }
    )
    diagnostic = _diagnostics(
        normalized.model_copy(update={"tables": (first, second)}),
        result,
        elapsed_seconds=0.0,
    )

    assert diagnostic.normalization_warnings == (
        "adjacent pages contain independent table candidates; "
        "cross-page continuity was not inferred",
    )

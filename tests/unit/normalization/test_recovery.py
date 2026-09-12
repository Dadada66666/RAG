from pathlib import Path

import pytest
from tests.parser_fixture import load_contract_result, normalization_context, profile_for_result
from tests.pdf_factory import write_tiny_pdf
from tests.quality_factory import calibration_profile
from tests.retrieval_factory import CharacterTokenizer
from tests.unit.application.test_parsing import ContractFixtureParser

from docparser.application.parsing import ParsingConfig, parse_document_with_diagnostics
from docparser.domain.parser_contract import ExtractedElementType, PageParseResult
from docparser.normalization.neutral import normalize_neutral_result
from docparser.normalization.recovery import normalize_recoverable_result
from docparser.quality import DeterministicQualityGate, QualityDecision, ValidationRequest
from docparser.retrieval.chunking import fixed_token_chunks, retrieval_evidence_view


@pytest.mark.parametrize("failure", ["overlap", "bounds"])
def test_bad_table_degrades_locally_and_preserves_raw_cell_observations(failure: str) -> None:
    result = load_contract_result("simple-table")
    page = result.pages[0]
    table = page.tables[0]
    cell = table.cells[1].model_copy(
        update={
            "row_index": 0 if failure == "overlap" else table.row_count,
            "column_index": 0,
        }
    )
    broken = table.model_copy(update={"cells": (table.cells[0], cell, *table.cells[2:])})
    result = result.model_copy(
        update={
            "pages": (
                page.model_copy(
                    update={
                        "tables": (broken,),
                        "elements": tuple(
                            element.model_copy(update={"text": ""})
                            if element.source_object_id == table.source_object_id
                            else element
                            for element in page.elements
                        ),
                    }
                ),
            )
        }
    )
    context = normalization_context(profile_for_result(result))
    with pytest.raises(ValueError):
        normalize_neutral_result(result, context)
    recovered = normalize_recoverable_result(result, context)
    assert not recovered.document.tables
    source = next(
        block for block in recovered.document.pages[0].blocks if block.block_type.value == "UNKNOWN"
    )
    assert source.text == "\n".join(value.text for value in broken.cells)
    assert source.block_id in retrieval_evidence_view(recovered.document).expected_source_block_ids
    assert recovered.warnings
    # Original parser evidence is untouched.
    assert result.pages[0].tables[0].cells[1] == cell
    report = DeterministicQualityGate().evaluate(
        ValidationRequest(recovered.document, context.profile, calibration_profile(), "test")
    )
    assert report.decision is QualityDecision.REJECT


def test_missing_page_is_explicit_and_does_not_erase_the_good_page() -> None:
    result = load_contract_result("born-digital")
    page = result.pages[0]
    second = PageParseResult(
        page_number=2, width=page.width, height=page.height, rotation=0, elements=(), tables=()
    )
    complete = result.model_copy(update={"pages_requested": (1, 2), "pages": (page, second)})
    partial = complete.model_copy(update={"pages": (page,)})
    recovered = normalize_recoverable_result(
        partial, normalization_context(profile_for_result(complete))
    )
    assert recovered.document.pages[0].blocks
    assert not recovered.document.pages[1].blocks
    recovery = recovered.document.extensions["org.docparser.recovery"]
    assert isinstance(recovery, dict)
    assert recovery["missing_pages"] == [2]
    record = next(record for record in recovered.document.provenance if record.page_number == 2)
    assert record.source_parser is None
    assert record.operation == "MISSING_PAGE_PLACEHOLDER"
    assert not recovered.document.quality_summary.publishable


def test_unknown_text_is_only_retrievable_after_explicit_recovery() -> None:
    result = load_contract_result("born-digital")
    page = result.pages[0]
    element = page.elements[-1].model_copy(update={"element_type": ExtractedElementType.UNKNOWN})
    result = result.model_copy(
        update={
            "pages": (
                page.model_copy(
                    update={
                        "elements": (*page.elements[:-1], element),
                    }
                ),
            )
        }
    )
    context = normalization_context(profile_for_result(result))
    strict = normalize_neutral_result(result, context)
    recovered = normalize_recoverable_result(result, context).document
    identifier = strict.pages[0].blocks[-1].block_id
    assert identifier not in retrieval_evidence_view(strict).expected_source_block_ids
    assert identifier in retrieval_evidence_view(recovered).expected_source_block_ids
    assert recovered.pages[0].blocks[-1].block_type.value == "UNKNOWN"


def test_valid_input_fixed_representation_does_not_change() -> None:
    result = load_contract_result("simple-table")
    context = normalization_context(profile_for_result(result))
    strict = normalize_neutral_result(result, context)
    recovered = normalize_recoverable_result(result, context)
    tokenizer = CharacterTokenizer()
    assert not recovered.warnings
    assert fixed_token_chunks(strict, tokenizer) == fixed_token_chunks(
        recovered.document, tokenizer
    )


def test_raw_parse_survives_strict_normalization_failure(tmp_path: Path) -> None:
    result = load_contract_result("simple-table")
    page = result.pages[0]
    table = page.tables[0].model_copy(update={"row_count": 1})
    result = result.model_copy(update={"pages": (page.model_copy(update={"tables": (table,)}),)})
    with pytest.raises(ValueError):
        parse_document_with_diagnostics(
            write_tiny_pdf(tmp_path / "source.pdf"),
            ParsingConfig(),
            parser=ContractFixtureParser(result),
            raw_output_dir=tmp_path / "raw",
            profile_provider=lambda _: profile_for_result(result),
        )
    assert (tmp_path / "raw" / "parse-result.json").is_file()
    assert (tmp_path / "raw" / "preflight.json").is_file()

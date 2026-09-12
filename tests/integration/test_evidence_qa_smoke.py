"""Opt-in server check: real local BGE-M3 and SiliconFlow, synthetic evidence only."""

import os
from pathlib import Path

import pytest
from tests.retrieval_factory import make_retrieval_document

from docparser.application.qa import ask_document
from docparser.retrieval.answering import SiliconFlowChatModel, SiliconFlowConfig
from docparser.retrieval.dense import BgeM3Runtime
from docparser.retrieval.index import build_evidence_index, load_evidence_index


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("DOCPARSER_RUN_QA_SMOKE") != "1",
    reason="opt-in real model test; sends only synthetic fixture text",
)
def test_real_bge_and_siliconflow_answer_a_synthetic_table(tmp_path: Path) -> None:
    runtime = BgeM3Runtime(
        Path(os.environ["BGE_M3_MODEL_PATH"]), device=os.environ.get("BGE_M3_DEVICE", "cpu")
    )
    build_evidence_index((make_retrieval_document(),), runtime, tmp_path / "index")
    session = load_evidence_index(tmp_path / "index").session(runtime)
    result = ask_document(
        "According to the table, what is Revenue? Answer with the number only.",
        session,
        model=SiliconFlowChatModel(
            SiliconFlowConfig(model=os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3.8-27B"))
        ),
    )
    (tmp_path / "synthetic.qa.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    assert result.answer is not None and result.answer.status == "ANSWERED"
    assert result.answer.claims[0].text.strip().rstrip(".") == "120"
    assert any(
        "120" in citation.quote for claim in result.answer.claims for citation in claim.citations
    )
    assert result.answer.usage.get("total_tokens", 0) > 0

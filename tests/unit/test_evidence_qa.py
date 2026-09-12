import io
import json
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document

from docparser.application.qa import ask_document
from docparser.retrieval.answering import (
    ChatRuntimeError,
    Completion,
    SiliconFlowChatModel,
    answer_from_context,
)
from docparser.retrieval.context import ContextConfig
from docparser.retrieval.index import build_evidence_index, load_evidence_index


class CitingModel:
    def __init__(self, evidence_id: str = "E1", quote: str | None = None) -> None:
        self.evidence_id = evidence_id
        self.quote = quote

    def complete(self, system: str, user: str) -> Completion:
        evidence = json.loads(user)["evidence"]
        quote = self.quote or evidence.split("\n", 1)[1].split("\n\n")[0]
        return Completion(
            json.dumps(
                {
                    "status": "ANSWERED",
                    "claims": [
                        {
                            "text": "Fixture answer",
                            "citations": [{"evidence_id": self.evidence_id, "quote": quote}],
                        }
                    ],
                    "reason": None,
                }
            ),
            "fixture-model",
            {"total_tokens": 30},
        )


def test_saved_index_reuses_document_embeddings_and_answers_with_sources(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    index = build_evidence_index((make_retrieval_document(),), runtime, tmp_path / "index")
    assert len(runtime.calls) == 1
    session = load_evidence_index(tmp_path / "index").session(runtime)
    result = ask_document("Revenue?", session, model=CitingModel())
    assert len(runtime.calls) == 2
    assert runtime.calls[-1] == ("Revenue?",)
    assert result.answer is not None and result.answer.status == "ANSWERED"
    assert result.answer.source_validation == "EXACT_QUOTES_CHECKED"
    assert result.answer.semantic_support_verified is False
    citation = result.answer.claims[0].citations[0]
    assert citation.locations and citation.source_digest
    evidence = next(
        item for item in result.context.evidence if item.evidence_id == citation.evidence_id
    )
    assert evidence.text[citation.quote_start : citation.quote_end] == citation.quote
    second = ask_document("Risk?", session)
    assert second.answer is None
    assert runtime.calls[-1] == ("Risk?",)
    assert len(runtime.calls) == 3
    assert index.manifest.chunk_count == len(session.chunks)


@pytest.mark.parametrize(
    ("identifier", "quote", "reason"),
    [
        ("E9999", "Revenue", "UNKNOWN_EVIDENCE_ID"),
        ("E1", "This sentence does not exist.", "QUOTE_NOT_IN_SUBMITTED_EVIDENCE"),
        ("E1", " ", "QUOTE_NOT_IN_SUBMITTED_EVIDENCE"),
    ],
)
def test_invalid_model_citations_are_not_published(
    tmp_path: Path, identifier: str, quote: str, reason: str
) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    result = ask_document("Revenue?", session, model=CitingModel(identifier, quote))
    assert result.answer is not None
    assert result.answer.status == "INVALID_RESPONSE"
    assert not result.answer.claims
    assert result.answer.reason == reason


def test_empty_context_abstains_without_a_remote_call(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    context = session.context(session.retrieve("Revenue?"), ContextConfig(max_tokens=1))

    class NeverCall:
        def complete(self, system: str, user: str) -> Completion:
            raise AssertionError("empty context must not call a model")

    answer = answer_from_context("Revenue?", context, NeverCall())
    assert answer.status == "INSUFFICIENT_EVIDENCE"
    assert answer.model is None


def test_index_detects_changed_inputs_and_model(tmp_path: Path) -> None:
    runtime = FakeEmbeddingRuntime()
    build_evidence_index((make_retrieval_document(),), runtime, tmp_path)
    loaded = load_evidence_index(tmp_path)
    loaded.manifest = loaded.manifest.model_copy(update={"model_digest": "changed"})
    with pytest.raises(ValueError, match="rebuild"):
        loaded.session(runtime)
    with (tmp_path / "sources.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="changed or incomplete"):
        load_evidence_index(tmp_path)


def test_siliconflow_request_contract_and_credentials_are_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-only-key")
    captured: dict[str, Any] = {}

    def fake_open(request: Any, *, timeout: float) -> io.BytesIO:
        captured["body"] = json.loads(request.data)
        captured["authorization"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        return io.BytesIO(
            json.dumps(
                {
                    "model": "fixture",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            ).encode()
        )

    monkeypatch.setattr("docparser.retrieval.answering.urlopen", fake_open)
    model = SiliconFlowChatModel()
    completion = model.complete("system", "question")
    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["enable_thinking"] is False
    assert captured["authorization"] == "Bearer test-only-key"
    assert "test-only-key" not in model.config.model_dump_json()
    assert completion.usage["total_tokens"] == 10
    monkeypatch.delenv("SILICONFLOW_API_KEY")
    with pytest.raises(ValueError, match="SILICONFLOW_API_KEY"):
        model.complete("system", "question")


def test_provider_error_does_not_echo_secret_or_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-only-key")

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise HTTPError("url", 401, "private error", Message(), io.BytesIO(b"secret document"))

    monkeypatch.setattr("docparser.retrieval.answering.urlopen", unavailable)
    with pytest.raises(RuntimeError, match="HTTP 401") as failure:
        SiliconFlowChatModel().complete("system", "private document")
    assert "private" not in str(failure.value)
    assert "test-only-key" not in str(failure.value)


def test_transport_timeout_is_a_classified_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-only-key")

    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("private provider message")

    monkeypatch.setattr("docparser.retrieval.answering.urlopen", timeout)
    with pytest.raises(ChatRuntimeError) as failure:
        SiliconFlowChatModel().complete("system", "question")
    assert failure.value.code == "TRANSPORT_ERROR"
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("body", [b"not JSON", b'{"choices":[]}', b'{"choices":[{}]}'])
def test_malformed_provider_envelope_is_classified(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-only-key")
    monkeypatch.setattr(
        "docparser.retrieval.answering.urlopen", lambda *args, **kwargs: io.BytesIO(body)
    )
    with pytest.raises(ChatRuntimeError) as failure:
        SiliconFlowChatModel().complete("system", "question")
    assert failure.value.code == "PROVIDER_PROTOCOL_ERROR"

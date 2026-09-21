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


@pytest.mark.parametrize("bad_quote", [False, True])
def test_answer_can_omit_reason_without_weakening_citations(
    tmp_path: Path, bad_quote: bool
) -> None:
    class WithoutReason(CitingModel):
        def complete(self, system: str, user: str) -> Completion:
            completion = super().complete(system, user)
            payload = json.loads(completion.text)
            del payload["reason"]
            return Completion(json.dumps(payload), completion.model, completion.usage)

    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    model = WithoutReason(quote="Revenue ... invented continuation" if bad_quote else None)
    result = ask_document("Revenue?", session, model=model)
    assert result.answer is not None
    assert result.answer.status == ("INVALID_RESPONSE" if bad_quote else "ANSWERED")
    if bad_quote:
        assert result.answer.reason == "QUOTE_NOT_IN_SUBMITTED_EVIDENCE"
        assert not result.answer.claims
    else:
        assert result.answer.reason is None
        assert result.answer.source_validation == "EXACT_QUOTES_CHECKED"


@pytest.mark.parametrize("reason", [{}, {"reason": None}, {"reason": ""}, {"reason": " "}])
def test_abstention_still_requires_a_meaningful_reason(reason: dict[str, Any]) -> None:
    from pydantic import ValidationError

    from docparser.retrieval.answering import AnswerDraft

    with pytest.raises(ValidationError):
        AnswerDraft.model_validate({"status": "INSUFFICIENT_EVIDENCE", "claims": [], **reason})


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
    assert result.answer.diagnostic is not None
    assert result.answer.diagnostic.error_types == (reason,)
    assert json.loads(result.answer.diagnostic.raw_completion)["claims"]


@pytest.mark.parametrize(
    "quote",
    [
        "Revenue increased strongly in 2025.",
        "Revenue\tincreased\r\nstrongly in 2025.",
        "Revenue\u00a0increased  strongly in 2025.",
    ],
)
def test_citation_whitespace_layout_is_canonicalized_to_original_source(
    tmp_path: Path, quote: str
) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    result = ask_document("Revenue?", session, model=CitingModel("E2", quote))

    assert result.answer is not None and result.answer.status == "ANSWERED"
    citation = result.answer.claims[0].citations[0]
    evidence = next(
        item for item in result.context.evidence if item.evidence_id == citation.evidence_id
    )
    assert evidence.text[citation.quote_start : citation.quote_end] == citation.quote
    if quote == "Revenue increased strongly in 2025.":
        assert result.answer.source_validation == "EXACT_QUOTES_CHECKED"
        assert "CITATION_CANONICAL_WHITESPACE_MATCH" not in result.answer.warnings
    else:
        assert result.answer.source_validation == "CANONICAL_WHITESPACE_QUOTES_CHECKED"
        assert "CITATION_CANONICAL_WHITESPACE_MATCH" in result.answer.warnings


@pytest.mark.parametrize(
    "quote",
    [
        "Revenue increased strongly in 2024.",
        "Revenue decreased strongly in 2025.",
        "revenue increased strongly in 2025.",
        "Revenue increased strongly ... in 2025.",
    ],
)
def test_citation_whitespace_matching_does_not_relax_semantic_characters(
    tmp_path: Path, quote: str
) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    result = ask_document("Revenue?", session, model=CitingModel(quote=quote))

    assert result.answer is not None
    assert result.answer.status == "INVALID_RESPONSE"
    assert result.answer.reason == "QUOTE_NOT_IN_SUBMITTED_EVIDENCE"


def test_canonical_whitespace_quote_uses_deterministic_first_original_interval(
    tmp_path: Path
) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)
    result = ask_document(
        "Revenue?",
        session,
        model=CitingModel("E2", "Revenue\tincreased strongly in 2025."),
    )

    assert result.answer is not None and result.answer.status == "ANSWERED"
    citation = result.answer.claims[0].citations[0]
    assert citation.quote == "Revenue increased strongly in 2025."


@pytest.mark.parametrize("raw", ["not JSON", '{"status":"ANSWERED","claims":[],"reason":null}'])
def test_invalid_response_preserves_raw_and_validation_details(tmp_path: Path, raw: str) -> None:
    runtime = FakeEmbeddingRuntime()
    session = build_evidence_index((make_retrieval_document(),), runtime, tmp_path).session(runtime)

    class InvalidModel:
        def complete(self, system: str, user: str) -> Completion:
            return Completion(raw, "fixture", {})

    result = ask_document("Revenue?", session, model=InvalidModel())
    assert result.answer is not None and result.answer.diagnostic is not None
    assert result.answer.status == "INVALID_RESPONSE"
    assert result.answer.diagnostic.raw_completion == raw
    assert result.answer.diagnostic.error_types


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

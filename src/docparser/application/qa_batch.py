"""A fixed question manifest and complete per-question QA records, including provider failures."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field

from docparser.application.qa import QAResult, ask_document
from docparser.ir.base import StrictIRModel
from docparser.ir.types import NonEmptyNfcString
from docparser.retrieval.answering import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ChatModel,
    SiliconFlowConfig,
)
from docparser.retrieval.context import ContextConfig
from docparser.retrieval.index import IndexManifest, QASearchSession, _file_digest


class QAQuestion(StrictIRModel):
    query_id: NonEmptyNfcString
    question: NonEmptyNfcString
    document_ids: tuple[str, ...] = ()


class QABatchConfig(StrictIRModel):
    top_k: int = Field(default=5, ge=1)
    context: ContextConfig = Field(default_factory=ContextConfig)
    generation: SiliconFlowConfig = Field(default_factory=SiliconFlowConfig)


class QABatchManifest(StrictIRModel):
    version: Literal["qa-batch@1.0.0"] = "qa-batch@1.0.0"
    status: Literal["RUNNING", "COMPLETE"]
    questions: tuple[QAQuestion, ...]
    index_manifest: IndexManifest
    config: QABatchConfig
    prompt_version: str = PROMPT_VERSION
    prompt_digest: str
    result_digests: dict[str, str] = Field(default_factory=dict)


def _write_record(path: Path, record: StrictIRModel) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _validate_questions(questions: tuple[QAQuestion, ...]) -> None:
    if not questions or len({item.query_id for item in questions}) != len(questions):
        raise ValueError("batch requires a nonempty manifest with unique query IDs")
    if any(not item.question.strip() or not item.query_id.strip() for item in questions):
        raise ValueError("query IDs and questions must contain text")


def run_qa_batch(
    questions: tuple[QAQuestion, ...],
    session: QASearchSession,
    model: ChatModel,
    output: Path,
    config: QABatchConfig,
) -> QABatchManifest:
    """Use one session; unexpected program errors leave a visibly incomplete run."""
    _validate_questions(questions)
    if any(set(item.document_ids) - session.document_ids for item in questions):
        raise ValueError("batch contains document IDs not present in this index")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("batch output is not empty; use a new directory")
    output.mkdir(parents=True, exist_ok=True)
    manifest = QABatchManifest(
        status="RUNNING",
        questions=questions,
        index_manifest=session.index.manifest,
        config=config,
        prompt_digest="sha256:" + hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
    )
    _write_record(output / "run.json", manifest)
    for ordinal, item in enumerate(questions):
        result = ask_document(
            str(item.question),
            session,
            model=model,
            top_k=config.top_k,
            context_config=config.context,
            document_ids=item.document_ids,
            query_id=str(item.query_id),
        )
        path = output / f"{ordinal:05}.qa.json"
        _write_record(path, result)
        manifest = manifest.model_copy(
            update={
                "result_digests": {**manifest.result_digests, item.query_id: _file_digest(path)}
            }
        )
        _write_record(output / "run.json", manifest)
    manifest = manifest.model_copy(update={"status": "COMPLETE"})
    _write_record(output / "run.json", manifest)
    return manifest


def load_qa_batch(path: Path) -> tuple[QABatchManifest, tuple[QAResult, ...]]:
    """Only complete, unchanged batches qualify for manifest-based evaluation."""
    manifest = QABatchManifest.model_validate_json((path / "run.json").read_bytes())
    _validate_questions(manifest.questions)
    expected_ids = {item.query_id for item in manifest.questions}
    if manifest.status != "COMPLETE" or set(manifest.result_digests) != expected_ids:
        raise ValueError("batch is incomplete; missing requests cannot disappear from evaluation")
    expected_files = {f"{ordinal:05}.qa.json" for ordinal in range(len(manifest.questions))}
    if {file.name for file in path.glob("*.qa.json")} != expected_files:
        raise ValueError("batch result files differ from the question manifest")
    results: list[QAResult] = []
    for ordinal, item in enumerate(manifest.questions):
        file = path / f"{ordinal:05}.qa.json"
        if _file_digest(file) != manifest.result_digests[item.query_id]:
            raise ValueError("batch result changed after completion")
        result = QAResult.model_validate_json(file.read_bytes())
        if (
            result.retrieval.benchmark_query_id != item.query_id
            or result.question != item.question
            or result.document_ids != tuple(sorted(set(item.document_ids)))
            or result.index_manifest != manifest.index_manifest
            or result.context.config != manifest.config.context
        ):
            raise ValueError("batch result identity, scope or configuration differs from manifest")
        if result.answer is None and result.execution_error is None:
            raise ValueError("context-only output is not a completed batch answer")
        results.append(result)
    return manifest, tuple(results)

"""Check local PDFs/IR and optional BGE tokenizer without loading model weights or queries."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from docparser.ir.invariants import validate_document_invariants
from docparser.ir.serialization import load_canonical_json
from docparser.preflight import inspect_pdf
from docparser.retrieval.chunking import fixed_token_chunks, retrieval_evidence_view
from docparser.retrieval.context import ContextBuilder, ContextConfig, prepare_sources
from docparser.retrieval.dense import QueryRetrieval, RetrievedChunk, _SentenceTransformerTokenizer


def check_ir(path: Path, tokenizer: _SentenceTransformerTokenizer | None) -> dict[str, Any]:
    document = load_canonical_json(path.read_bytes())
    validate_document_invariants(document)
    view = retrieval_evidence_view(document)
    record: dict[str, Any] = {
        "path": str(path),
        "status": "PASS",
        "document_id": str(document.document_id),
        "revision_id": str(document.revision_id),
        "pages": document.page_count,
        "tables": len(document.tables),
        "sections": len(document.sections),
        "ordered_blocks": len(view.ordered_blocks),
        "isolated_blocks": len(view.isolated_unresolved_blocks),
        "unrenderable_blocks": len(view.unrenderable_blocks),
        "tables_with_explicit_column_headers": sum(
            bool(t.header_row_indices) for t in document.tables
        ),
        "tables_with_bound_captions": sum(bool(t.caption_block_ids) for t in document.tables),
    }
    if tokenizer is None:
        record["context_probe"] = "SKIP_NO_TOKENIZER"
        return record
    chunks = tuple(c for c in fixed_token_chunks(document, tokenizer) if c.embedding_eligible)
    covered = {block for chunk in chunks for block in chunk.source_block_ids}
    if covered != set(view.expected_source_block_ids):
        raise ValueError("Fixed source coverage differs from the renderable evidence universe")
    sources, spans = prepare_sources(document, tokenizer, chunks)
    record["fixed_chunks"] = len(chunks)
    record["alignment_counts"] = dict(
        Counter(s.table_map.alignment for s in sources.values() if s.table_map is not None)
    )
    builder = ContextBuilder(sources, tokenizer)
    # Deterministic injected hits exercise context restoration; these are NOT retrieval results.
    table_hits = dict.fromkeys(
        str(chunk.chunk_id)
        for source in sources.values()
        if source.kind == "TABLE"
        for chunk in chunks
        if source.source_id in chunk.source_block_ids
    )
    by_id = {str(chunk.chunk_id): chunk for chunk in chunks}
    probes = []
    for identifier in table_hits:
        chunk = by_id[identifier]
        retrieval = QueryRetrieval(
            benchmark_query_id="cpu-context-probe",
            document_name="probe",
            hits=(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    document_name="probe",
                    rank=1,
                    score=0.0,
                    page_numbers=(chunk.page_start,),
                ),
            ),
        )
        context = builder.build(retrieval, spans, ContextConfig(table_policy="LOGICAL_ROWS"))
        if context.token_count > context.budget_tokens:
            raise ValueError("context exceeds its declared budget")
        for item in context.evidence:
            if item.char_start is not None:
                if item.text != sources[item.source_id].text[item.char_start : item.char_end]:
                    raise ValueError("restored row text differs from its source character interval")
        probes.append(
            {
                "chunk_id": identifier,
                "context_tokens": context.token_count,
                "restored_row_evidence": sum(bool(item.row_indices) for item in context.evidence),
                "incomplete_row_evidence": sum(
                    item.row_band_complete is False for item in context.evidence
                ),
                "omitted_source_count": len(context.omitted_source_ids),
                "warnings": sorted(
                    {w for item in context.evidence for w in item.warnings} | set(context.warnings)
                ),
            }
        )
    record["context_probe"] = "INJECTED_HITS_NOT_RETRIEVAL_EVALUATION"
    record["probes"] = probes
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir-root", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.ir_root, args.pdf_root):
        if not path.is_dir():
            parser.error(f"input directory not found: {path}")
    if args.output.exists():
        parser.error("output exists; preserve prior reports and choose a new path")
    os.environ.update(
        CUDA_VISIBLE_DEVICES="",
        USE_TORCH="0",
        USE_TF="0",
        USE_FLAX="0",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    tokenizer = None
    tokenizer_status = "SKIP_NOT_CONFIGURED"
    if args.tokenizer_path is not None:
        transformers = importlib.import_module("transformers")
        native = transformers.AutoTokenizer.from_pretrained(
            str(args.tokenizer_path), local_files_only=True, use_fast=True
        )
        tokenizer = _SentenceTransformerTokenizer(native, f"cpu-probe:{args.tokenizer_path}")
        tokenizer_status = "LOADED_NO_WEIGHTS"
    report: dict[str, Any] = {
        "scope": "CPU_ASSETS_AND_CONTEXT_CONTRACTS_ONLY",
        "tokenizer": tokenizer_status,
        "pdfs": [],
        "irs": [],
        "failures": 0,
    }
    started = perf_counter()
    for path in sorted(args.pdf_root.rglob("*.pdf")):
        try:
            profile = inspect_pdf(path)
            record = {
                "path": str(path),
                "status": "PASS",
                "pages": profile.page_count,
                "document_type": profile.document_type.value,
                "warnings": list(profile.warnings),
            }
        except (OSError, ValueError) as error:
            record = {"path": str(path), "status": "FAIL", "error": str(error).splitlines()[0]}
            report["failures"] += 1
        report["pdfs"].append(record)
        print(f"PDF {record['status']} {path}", flush=True)
    for path in sorted(args.ir_root.rglob("document.ir.json")):
        try:
            record = check_ir(path, tokenizer)
        except (OSError, ValueError) as error:
            record = {"path": str(path), "status": "FAIL", "error": str(error).splitlines()[0]}
            report["failures"] += 1
        report["irs"].append(record)
        print(f"IR {record['status']} {path}", flush=True)
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    report["missing_inputs"] = [key for key in ("pdfs", "irs") if not report[key]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {args.output}", flush=True)
    return 1 if report["failures"] or report["missing_inputs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

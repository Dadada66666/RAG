from pathlib import Path

import pytest
from tests.retrieval_factory import FakeEmbeddingRuntime, make_retrieval_document

from docparser.ir.enums import BlockType, ReadingOrderStatus
from docparser.ir.geometry import BBox
from docparser.ir.models import DocumentIR
from docparser.retrieval.caption_links import associate_table_captions
from docparser.retrieval.context import ContextBuilder, ContextConfig, SourceSpan
from docparser.retrieval.dense import QueryRetrieval, RetrievedChunk
from docparser.retrieval.index import build_evidence_index, load_evidence_index


def document(*, below: bool = False, competing: bool = False) -> DocumentIR:
    doc = make_retrieval_document()
    page = doc.pages[0]
    caption = page.blocks[2].model_copy(
        update={
            "block_type": BlockType.FIGURE_CAPTION,
            "text": "Table 8: Regional revenue",
            "bbox": BBox((30.0, 410.0 if below else 270.0, 270.0, 430.0 if below else 290.0)),
            "reading_order": None,
            "reading_order_status": ReadingOrderStatus.UNRESOLVED,
        }
    )
    table = page.blocks[3].model_copy(update={"bbox": BBox((30.0, 300.0, 270.0, 400.0))})
    blocks = [caption, table]
    if competing:
        blocks.append(
            page.blocks[0].model_copy(
                update={
                    "block_type": BlockType.TABLE,
                    "content_ref": table.content_ref,
                    "bbox": BBox((30.0, 150.0, 270.0, 260.0)),
                }
            )
        )
    return doc.model_copy(
        update={"pages": (page.model_copy(update={"blocks": tuple(blocks)}), doc.pages[1])}
    )


@pytest.mark.parametrize("below", [False, True])
def test_unique_caption_above_or_below_is_bidirectional(below: bool) -> None:
    doc = document(below=below)
    caption, table = doc.pages[0].blocks
    before = doc.model_dump_json()
    links = associate_table_captions(doc, frozenset(str(b.block_id) for b in doc.pages[0].blocks))
    assert links[str(caption.block_id)][0].target_source_id == str(table.block_id)
    assert links[str(table.block_id)][0].basis == "DERIVED_UNIQUE_GEOMETRY"
    assert doc.model_dump_json() == before


def test_ambiguity_abstains_instead_of_selecting_nearest_table() -> None:
    doc = document(competing=True)
    assert not associate_table_captions(
        doc, frozenset(str(b.block_id) for b in doc.pages[0].blocks)
    )


def test_explicit_binding_wins_without_fabricated_geometry() -> None:
    doc = document(competing=True)
    caption, table, _ = doc.pages[0].blocks
    doc = doc.model_copy(
        update={
            "tables": (doc.tables[0].model_copy(update={"caption_block_ids": (caption.block_id,)}),)
        }
    )
    links = associate_table_captions(doc, frozenset(str(b.block_id) for b in doc.pages[0].blocks))
    assert links[str(table.block_id)][0].basis == "EXPLICIT"
    assert links[str(table.block_id)][0].gap_points is None


def test_cross_page_caption_is_not_inferred() -> None:
    doc = document()
    caption, table = doc.pages[0].blocks
    doc = doc.model_copy(
        update={
            "pages": (
                doc.pages[0].model_copy(update={"blocks": (table,)}),
                doc.pages[1].model_copy(
                    update={"blocks": (caption.model_copy(update={"page_number": 2}),)}
                ),
            )
        }
    )
    assert not associate_table_captions(
        doc, frozenset((str(caption.block_id), str(table.block_id)))
    )


@pytest.mark.parametrize("change", ["column", "paragraph", "far", "label"])
def test_unrelated_text_is_not_bound(change: str) -> None:
    doc = document()
    caption, table = doc.pages[0].blocks
    changes: dict[str, dict[str, object]] = {
        "column": {"bbox": BBox((320.0, 270.0, 570.0, 290.0))},
        "paragraph": {"block_type": BlockType.PARAGRAPH},
        "far": {"bbox": BBox((30.0, 100.0, 270.0, 120.0))},
        "label": {"text": "Figure 8: Revenue"},
    }
    caption = caption.model_copy(update=changes[change])
    doc = doc.model_copy(
        update={"pages": (doc.pages[0].model_copy(update={"blocks": (caption, table)}),)}
    )
    assert not associate_table_captions(
        doc, frozenset((str(caption.block_id), str(table.block_id)))
    )


def test_caption_only_hit_restores_table_without_new_embedding(tmp_path: Path) -> None:
    doc = document()
    runtime = FakeEmbeddingRuntime()
    index = build_evidence_index((doc,), runtime, tmp_path)
    index = load_evidence_index(tmp_path)
    caption, table = doc.pages[0].blocks
    entry = index.entries[0]
    retrieval = QueryRetrieval(
        benchmark_query_id="unseen",
        document_name="corpus",
        hits=(
            RetrievedChunk(
                chunk_id=entry.chunk.chunk_id,
                document_name="fixture",
                rank=1,
                score=0.9,
                page_numbers=(1,),
            ),
        ),
    )
    spans: dict[str, tuple[SourceSpan, ...]] = {
        str(entry.chunk.chunk_id): (
            SourceSpan(
                source_id=str(caption.block_id), token_start=0, token_end=len(caption.text or "")
            ),
        )
    }
    builder = ContextBuilder(index.sources, runtime.tokenizer)
    baseline = builder.build(retrieval, spans, ContextConfig(include_related=False))
    restored = builder.build(
        retrieval, spans, ContextConfig(caption_context=True, include_related=False)
    )
    assert not any(e.source_id == str(table.block_id) for e in baseline.evidence)
    assert any(e.source_id == str(table.block_id) and "120" in e.text for e in restored.evidence)
    assert any("DERIVED_CAPTION_ASSOCIATION" in w for e in restored.evidence for w in e.warnings)
    assert len(runtime.calls) == 1
    limited = builder.build(retrieval, spans, ContextConfig(caption_context=True, max_tokens=180))
    assert limited.token_count <= 180
    assert "CAPTION_ASSOCIATION_BUDGET_EXCEEDED" in limited.warnings

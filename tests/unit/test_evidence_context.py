from tests.retrieval_factory import CharacterTokenizer, make_retrieval_document
from tests.unit.test_retrieval_chunking import _with_multisegment_table

from docparser.ir.chunks import Chunk
from docparser.ir.models import DocumentIR
from docparser.retrieval.chunking import FixedChunkConfig, fixed_token_chunks
from docparser.retrieval.context import ContextBuilder, ContextConfig, SourceSpan, prepare_sources
from docparser.retrieval.dense import QueryRetrieval, RetrievedChunk


def prepared(
    document: DocumentIR, target: int = 64, overlap: int = 16
) -> tuple[ContextBuilder, tuple[Chunk, ...], dict[str, tuple[SourceSpan, ...]]]:
    tokenizer = CharacterTokenizer()
    chunks = tuple(
        chunk
        for chunk in fixed_token_chunks(
            document, tokenizer, FixedChunkConfig(target_tokens=target, overlap_tokens=overlap)
        )
        if chunk.embedding_eligible
    )
    sources, spans = prepare_sources(document, tokenizer, chunks)
    return ContextBuilder(sources, tokenizer), chunks, spans


def retrieved(chunks: tuple[Chunk, ...]) -> QueryRetrieval:
    return QueryRetrieval(
        benchmark_query_id="fixture",
        document_name="fixture",
        hits=tuple(
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_name="fixture",
                rank=rank,
                score=1.0 / rank,
                page_numbers=(chunk.page_start,),
            )
            for rank, chunk in enumerate(chunks, start=1)
        ),
    )


def test_complete_small_table_restored_and_sources_resolve() -> None:
    document = make_retrieval_document()
    builder, chunks, spans = prepared(document)
    table_id = str(document.tables[0].segments[0].block_id)
    hit = next(
        chunk
        for chunk in chunks
        if any(span.source_id == table_id for span in spans[str(chunk.chunk_id)])
    )
    context = builder.build(retrieved((hit,)), spans, ContextConfig(max_tokens=3000))
    table = next(item for item in context.evidence if item.source_id == table_id)
    assert table.complete_source
    assert "Revenue" in table.text and "120" in table.text and "Profit" in table.text
    source = next(source for source in context.sources if source.source_id == table_id)
    assert source.locations[0].precision == "TABLE_REGION"
    assert source.entity_id == str(document.tables[0].table_id)
    assert source.source_digest == str(document.source.sha256)
    assert context.token_count == len(builder.tokenizer.encode(context.text))


def test_overlap_is_merged_but_distant_spans_of_same_block_survive() -> None:
    document = make_retrieval_document()
    data = document.model_dump()
    data["pages"][0]["blocks"][2]["text"] = " ".join(f"word{i:03}" for i in range(150))
    document = DocumentIR.model_validate(data)
    source_id = str(document.pages[0].blocks[2].block_id)
    builder, chunks, spans = prepared(document)
    candidates = [
        chunk
        for chunk in chunks
        if any(span.source_id == source_id for span in spans[str(chunk.chunk_id)])
    ]
    selected = (candidates[1], candidates[2], candidates[-2])
    context = builder.build(
        retrieved(selected),
        spans,
        ContextConfig(
            max_tokens=10000, max_excerpt_tokens=64, expand_source_tokens=0, include_related=False
        ),
    )
    actual = [item for item in context.evidence if item.source_id == source_id]
    covered = [position for item in actual for position in range(item.token_start, item.token_end)]
    expected = {
        position
        for chunk in selected
        for span in spans[str(chunk.chunk_id)]
        if span.source_id == source_id
        for position in range(span.token_start, span.token_end)
    }
    assert set(covered) == expected
    assert len(covered) == len(set(covered))
    assert len(actual) >= 2


def test_exact_budget_reports_omissions_instead_of_silently_truncating() -> None:
    builder, chunks, spans = prepared(make_retrieval_document(), 32, 8)
    context = builder.build(
        retrieved(chunks),
        spans,
        ContextConfig(
            max_tokens=220, max_excerpt_tokens=32, expand_source_tokens=0, include_related=False
        ),
    )
    assert 0 < context.token_count <= 220
    assert context.omitted_source_ids
    assert "CONTEXT_BUDGET_OMISSIONS" in context.warnings
    empty = builder.build(retrieved(chunks), spans, ContextConfig(max_tokens=1))
    assert not empty.evidence
    assert empty.token_count == 0


def test_large_table_excerpt_discloses_incompleteness_and_adds_explicit_headers() -> None:
    document = make_retrieval_document()
    builder, chunks, spans = prepared(document, 24, 4)
    source_id = str(document.tables[0].segments[0].block_id)
    hits = [
        chunk
        for chunk in chunks
        if any(span.source_id == source_id for span in spans[str(chunk.chunk_id)])
    ]
    context = builder.build(
        retrieved((hits[-1],)), spans, ContextConfig(max_tokens=2000, expand_source_tokens=30)
    )
    table = next(item for item in context.evidence if item.source_id == source_id)
    assert not table.complete_source
    assert any("INCOMPLETE_TABLE_EXCERPT" in warning for warning in table.warnings)
    header = next(item for item in context.evidence if item.role == "RELATED")
    assert "Metric" in header.text and "Value" in header.text


def test_unresolved_content_does_not_acquire_invented_neighbor_context() -> None:
    document = make_retrieval_document()
    identifier = str(document.pages[0].blocks[-1].block_id)
    builder, chunks, spans = prepared(document)
    hit = next(chunk for chunk in chunks if identifier in chunk.source_block_ids)
    context = builder.build(retrieved((hit,)), spans)
    assert {item.source_id for item in context.evidence} == {identifier}


def test_blocks_from_one_hit_keep_the_original_stream_order() -> None:
    builder, chunks, spans = prepared(make_retrieval_document(), 512, 64)
    hit = chunks[0]
    context = builder.build(retrieved((hit,)), spans, ContextConfig(include_related=False))
    assert [item.source_id for item in context.evidence] == [
        span.source_id for span in spans[str(hit.chunk_id)]
    ]


def test_distinct_cross_page_segments_survive_with_precise_region_attribution() -> None:
    document = _with_multisegment_table(make_retrieval_document())
    builder, chunks, spans = prepared(document, 512, 64)
    context = builder.build(retrieved(chunks), spans)
    sources = {source.source_id: source for source in context.sources}
    table_evidence = [item for item in context.evidence if sources[item.source_id].kind == "TABLE"]
    assert len(table_evidence) == 2
    assert {
        loc.page_number for item in table_evidence for loc in sources[item.source_id].locations
    } == {1, 2}
    for item in table_evidence:
        table_source = sources[item.source_id]
        assert len(table_source.block_ids) == 1
        assert len(table_source.locations) == 1
        assert table_source.locations[0].precision == "TABLE_REGION"
    assert "Revenue" in table_evidence[0].text
    assert "Profit" in table_evidence[1].text


def test_continuation_segment_can_use_explicit_headers_from_first_page() -> None:
    document = _with_multisegment_table(make_retrieval_document())
    builder, chunks, spans = prepared(document, 24, 4)
    identifier = str(document.tables[0].segments[1].block_id)
    first_segment = str(document.tables[0].segments[0].block_id)
    hit = next(
        chunk
        for chunk in chunks
        if identifier in chunk.source_block_ids and first_segment not in chunk.source_block_ids
    )
    context = builder.build(retrieved((hit,)), spans)
    sources = {source.source_id: source for source in context.sources}
    header = next(
        item for item in context.evidence if sources[item.source_id].kind == "TABLE_HEADER_ROWS"
    )
    source = sources[header.source_id]
    assert header.role == "RELATED"
    assert source.kind == "TABLE_HEADER_ROWS"
    assert {loc.page_number for loc in source.locations} == {1}
    assert "Metric" in header.text

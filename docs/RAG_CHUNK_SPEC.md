# RAG Chunk Specification

| Field | Value |
|---|---|
| Status | Retrieval evidence contract closed over ordered and isolated evidence |
| Chunk schema version | `1.0.0` |
| Fixed chunker version | `ir-fixed-token@1.1.0` |
| Structure chunker version | `ir-structure-aware@2.1.0` |

## 1. Purpose

Chunks are deterministic retrieval views over a specific immutable IR revision. They are not a second source of truth. Every chunk must be reconstructible from its ordered source blocks and must resolve to page/bbox/source provenance.

The implemented experiment provides an unchanged Canonical-IR fixed-token control and a
relationship-bound semantic-packing treatment. Structure v2 uses explicit Section, Table caption,
column-header, logical-row and TableSegment evidence; it does not claim a globally optimal
hierarchy or chunk policy.

Fixed-size character splitting is prohibited as the primary policy because it:

- breaks headings from their content and loses hierarchy;
- splits tables/cells and detaches repeated headers;
- separates figures/equations from captions/context;
- crosses column/section boundaries despite visual reading order;
- measures characters rather than model tokens, especially poorly for Chinese/English mixtures;
- makes citation geometry ambiguous and changes unpredictably with encoding/whitespace.

## 2. Chunk model

```text
Chunk
├── chunk_id
├── document_id, ir_revision_id
├── chunk_schema_version, chunker_version, chunk_config_hash
├── chunk_type: PARENT | CHILD | TABLE | FIGURE | EQUATION | REFERENCE
├── parent_chunk_id
├── text
├── parent_section_id
├── heading_path[]
├── page_start, page_end
├── source_block_ids[]
├── source_entity_ids[]
├── bboxes[] {page_number, bbox}
├── content_types[]
├── token_count, tokenizer_id
├── content_digest, embedding_input_digest
├── embedding_eligible, sparse_eligible
├── metadata
└── provenance_ids[]
```

Required constraints:

- `source_block_ids` is ordered and non-empty for published chunks.
- `page_start/page_end` equal min/max resolved source pages.
- `bboxes` is a lossless per-page union list or per-block list by config; it cannot imply one bbox across pages.
- `heading_path` is derived from the IR section forest and includes only resolved headings.
- `text` is derived, not manually edited. Deterministic prefixes (heading/table header) are declared in metadata.
- `token_count` is produced by the exact pinned `tokenizer_id`; a tokenizer change changes chunk IDs/config hash.
- `content_digest` hashes canonical rendered chunk content; `embedding_input_digest` hashes the exact text/media reference presented to an embedding model after declared prefixes. They permit tenant/policy-scoped cache reuse without weakening revision-specific chunk identity or provenance.
- `metadata` is bounded, namespaced where custom, and must not duplicate full provenance.

### `ParentChunk`

A parent chunk is the retrievable/display context for one section or large semantic unit. It may be `embedding_eligible=false` if over the token limit. Child chunks point to exactly one parent. Parent text is assembled from the same IR revision; it is never an LLM summary in the core pipeline.

### `HeadingPath`

Ordered section headings from root to the chunk's owning section. The chunk renderer may prepend a compact heading path to child text; injected prefix tokens are recorded as `metadata.rendered_prefix` and are not given source bbox provenance independent of their heading blocks.

### `SourceBlocks` and provenance

`source_block_ids` identify exact IR blocks. `source_entity_ids` additionally references logical tables/figures/equations where the chunk represents them. Chunk provenance records operation `CHUNK_ASSEMBLY` with parent provenance IDs from all source blocks/entities.

## 3. Deterministic chunk pipeline

```text
validated IR revision
 -> build semantic unit stream from section tree + reading order
 -> bind captions/footnotes/equation context
 -> form parent units
 -> pack child units to token budget without crossing protected boundaries
 -> apply semantic overlap
 -> render text + metadata
 -> validate provenance/limits
 -> persist JSONL + manifest
```

### 3.1 Preconditions

- IR passes hard invariants and is publishable, or job is explicitly `PARTIAL` with disclosed gaps.
- Section tree, reading order and content relationships are the chosen revision's truth.
- Tokenizer/model files are locally pinned; no network lookup at chunk time.

### 3.2 Semantic units

Structure v2 first constructs semantic retrieval units rather than packing the raw block stream.
Atomic units:

- heading plus following content ownership marker;
- paragraph, quote, code block, list item (list may group children);
- logical table or deterministic table row band, with explicitly linked captions bound to the
  table rather than emitted as competing independent candidates;
- figure plus caption and nearby referring paragraph according to relationship policy;
- equation plus label/caption and configurable adjacent paragraph context;
- footnote attached to target when it fits, otherwise separate linked unit;
- reference entry.

Header/footer/page number blocks are excluded from normal retrieval text but remain available in IR. Inclusion is a versioned policy flag for use cases that require them.

The retrieval semantic stream is a dedicated derived view over Canonical IR. ParseBench/evaluation
renderers are interoperability views and must never be reused as the RAG semantic renderer. Only
`IN_FLOW` blocks with types in the canonical retrieval-flow allowlist are ordered retrievable
evidence. `UNRESOLVED` allowlisted blocks are unordered but still retrievable: each is rendered as
an isolated source stream and never participates in adjacency, overlap, cross-block packing, or
inferred Section ownership. `DECORATIVE` and `UNKNOWN` blocks remain in Canonical IR but are
excluded from the normal retrieval corpus. A semantic block with neither text nor renderable linked
entity content is reported as unrenderable rather than silently dropped.

Section is the trusted ordered structure view, not the complete retrieval inventory. The dedicated
Retrieval Evidence View combines Section-backed ordered evidence with isolated unresolved evidence.

### 3.3 Protected boundaries

Packing cannot cross:

- top-level sections by default;
- a table/figure/equation atomic boundary;
- unrelated columns when reading order is unresolved;
- page gaps/missing scopes in PARTIAL documents;
- language/structure boundaries declared by configured policy.

Paragraphs may only be split when one paragraph alone exceeds hard token limit. The split uses sentence boundaries from deterministic multilingual segmentation; if still oversized, token boundaries are used and the exceptional split is recorded.

## 4. Token budgets and packing

Implemented defaults:

```yaml
target_tokens: 512
hard_max_tokens: 8000
semantic_overlap_units: 1
```

Greedy ordered packing is deterministic:

1. Start with section heading prefix cost.
2. Append complete semantic units while <= target.
3. Preserve a single ordinary unit above target when it remains <= hard max.
4. Split a single ordinary text unit only when that unit exceeds hard max.
5. Never emit an embedding-eligible chunk above hard max; fail if a protected unit has no legal
   split.

The MVP stops at this deterministic greedy policy. It does not require a cost-function optimizer.
Any later optimizer must specify its objective and demonstrate an improvement on the retrieval
evaluation set; stable source-block order remains the tie-break.

## 5. Type-specific behavior

### 5.1 Headings and sections

- A heading is included with at least the first child content when possible; heading-only child chunks are avoided.
- Parent chunks align to sections. Very small sibling subsections may share a parent but child chunks do not cross top-level boundaries.
- Heading levels are metadata, not inferred again from font sizes.

### 5.2 Tables

Logical table atomicity means the structure is never flattened and arbitrarily cut mid-cell.

- If table rendering fits the target: one `TABLE` chunk with its explicit caption, headers and
  complete table.
- If oversized: retain the non-embedding Section parent and emit row-group `TABLE` chunks. Every
  table chunk repeats only rows identified by `COLUMN_HEADER`/`BOTH` cell roles, contains complete
  logical rows/cells, records `row_start/row_end`, and points to the same table entity. `ROW_HEADER`
  cells remain row stubs and `UNKNOWN` header roles are not promoted to column headers.
- A merged cell crossing row-group boundary is carried as contextual header/stub metadata or forces the boundary to move; it is never split into contradictory values.
- Explicit column headers render deterministic key-value data rows; without complete explicit
  column-header evidence, the serializer falls back to compact logical rows and does not guess.
- Cross-page segments do not force chunk breaks. Each row group binds only intersecting
  `TableSegment` blocks for page/bbox hit provenance; heading/caption sources remain declared
  context metadata.
- The selected serializer/version and repeated context are declared in metadata.

### 5.3 Figures

- A `FIGURE` chunk includes caption and optionally OCR-inside-figure text.
- Binary image is referenced through `asset_artifact_ids`, never embedded in chunk JSON.
- No generated figure description exists unless an optional, separately versioned enrichment produced a provenance-linked block. Core chunking does not call an LLM.
- Nearby referring paragraph may be included as context or linked by source block ID according to policy; it is not duplicated into unrelated chunks without metadata.

### 5.4 Equations and code

- Keep equation/code atomic up to hard max and include label plus nearest owning paragraph when possible.
- Use original normalized LaTeX/code; never paraphrase.
- An oversized code block uses line-aware splits with language/fence context repeated and recorded.

### 5.5 Lists, footnotes and references

- Keep a list item atomic; group adjacent items while respecting token limit and parent list.
- Attach footnote text to target chunk when relationship is confident and budget allows; otherwise emit a linked child chunk.
- Bibliographic references are one entry per chunk or small ordered groups, never split inside one entry unless oversized.

## 6. Overlap policy

Overlap is semantic, not a raw character window. Default overlap is the final complete semantic unit of the previous child, only when:

- it is not a table/figure atomic unit;
- it does not cross a protected boundary;
- duplication stays within token budget;
- metadata lists `overlap_source_block_ids`.

Heading prefixes may repeat without being counted as content overlap. Retrieval/evaluation can de-duplicate hits using source blocks and parent IDs.

## 7. Partial and low-quality documents

- `COMPLETED` chunks inherit `quality_status=PASS`.
- `PARTIAL` chunks carry missing/degraded page/scope issue IDs and never bridge across a missing region/page.
- Blocks with unresolved reading order may be emitted as separate chunks with `retrieval_warning`; they cannot be silently ordered geometrically across columns.
- Chunks are not produced from an IR with missing/broken provenance hard gates.

## 8. ID, versioning and invalidation

`chunk_id` is UUIDv5 over document ID, IR revision ID, chunk schema/chunker/tokenizer/config versions and ordered source block IDs. Any semantic source or policy change creates new IDs.

Incremental regeneration may reuse chunks only when all inputs and rendered text digest match. After fallback, invalidate:

- chunks containing changed source entities;
- overlap neighbors;
- owning parent chunks;
- downstream section metadata chunks if heading hierarchy changed.

Embedding computation may reuse a vector when `embedding_input_digest`, embedding model digest and embedding configuration all match inside the same authorized tenant/policy namespace. The new revision still receives its own chunk ID, metadata and provenance; vector reuse never reactivates stale page/bbox/quality metadata.

Old chunk manifests remain immutable and are withdrawn from downstream indexing by revision activation events.

## 9. Export contract

- Canonical format: manifest JSON + chunks JSONL.
- Each JSONL line independently validates and has bounded size.
- Export manifest includes document/revision, chunker/tokenizer/config versions, count, digest, quality report, created time and source IR artifact.
- Downstream ingestion must upsert by `(document_id, chunk_id)` and deactivate prior revision atomically where possible.
- A chunk manifest is activation-eligible only when its referenced quality report is PASS, or an explicit downstream policy accepts its PARTIAL issue set. `PARTIAL` is deny-by-default and cannot be made indistinguishable from PASS by the export API.
- Dense, sparse and hybrid indexes may render different text fields from the same chunk, but cannot drop provenance.

## 10. Validation and evaluation

### 10.1 Implemented A/B experiment

Before promotion, evaluate on the same versioned query/QA set:

- **Control:** the complete retrieval evidence universe, with the resolved stream using unchanged
  fixed-token windows and each unresolved block using an isolated fixed-token stream;
- **Experiment:** the same IR and tokenizer, with Section boundaries, heading context and protected
  logical Table row groups.

Both representations use the exact BGE-M3 tokenizer. Structure v2 adds one configurable complete
semantic-unit overlap within a Section, explicit Table-caption binding, header-aware row rendering
and precise row-segment provenance. It does not add LLM summaries, hierarchy inference, score
fusion or a vector database. Non-embedding Section parents retain complete context and are counted
component-wise so they are not sent through the tokenizer as one model-oversized input.

Before embedding, each document must satisfy exact source-evidence parity:
`expected == fixed covered == structure covered`. Coverage includes direct source blocks plus
explicitly rendered context and semantic-overlap sources. Canonical Table blocks must be covered by
both representations and must activate at least one Structure `TABLE` chunk when linked to a valid
Table entity.

Report retrieval, table retrieval, citation and latency deltas independently. Later optional
experiments may add contextual heading/path enrichment. Late Chunking is allowed only when the
chosen embedding model supports it and the same benchmark shows value. RAPTOR is deferred until a
multi-hop/document-level evaluation demonstrates need. The first RAG version must not implement
RAPTOR or use LLM-generated summaries as core parent chunks.

Hard validation:

- all source IDs/provenance resolve;
- token counts and limits match pinned tokenizer;
- ordering agrees with IR;
- table row groups cover rows exactly with only declared header repetition;
- no forbidden boundary crossing;
- bbox/page ranges match source entities;
- deterministic re-run yields identical semantic digest.

Quality evaluation:

- boundary precision/recall against annotated semantic units;
- semantic continuity and context sufficiency reviewer scores;
- orphan heading/caption/equation rate;
- table completeness and row-group coverage;
- provenance/citation resolvability and bbox accuracy;
- retrieval-oriented downstream proxy tasks, reported separately from parsing quality.

## 11. Failure modes and replacement

| Failure | Behavior |
|---|---|
| Tokenizer unavailable/digest mismatch | Non-retryable config error; no approximate count |
| Single cell/paragraph exceeds hard max | Type-specific deterministic split; issue recorded |
| Invalid section tree/order | Stop unless policy-approved PARTIAL isolated units |
| Missing figure asset | Text/caption chunk may exist with disclosed issue; no broken asset ref |
| Chunker regression | Golden/regression gate blocks promotion; old manifest remains active |

Chunkers implement a versioned `ChunkPolicy`/`Chunker` port and are replaceable through the same benchmark and manifest versioning discipline as parsers.

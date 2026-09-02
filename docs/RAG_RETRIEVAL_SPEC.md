# RAG Retrieval Specification

| Field | Value |
|---|---|
| Status | Authoritative MVP contract; not implemented |
| Contract version | `rag-retrieval/1.0.0` |
| Principle | Local, reproducible baseline first; replaceable backends |

## 1. Scope and pipeline

```text
Query
 -> deterministic query normalization (optional/versioned)
 -> dense retrieval + sparse retrieval
 -> Reciprocal Rank Fusion
 -> metadata-policy filter and source deduplication
 -> optional reranker
 -> parent/context expansion
 -> budgeted context assembly
 -> citation bundle
```

This layer consumes versioned derived chunks and immutable Canonical provenance. It does not parse,
repair or mutate documents and does not answer questions. The MVP supports local evaluation without
choosing a production vector-database brand.

## 2. Ports and versioned records

```python
class DenseIndex(Protocol):
    def upsert(self, records: Sequence[DenseRecord]) -> None: ...
    def search(self, request: DenseQuery) -> Sequence[RankedCandidate]: ...

class SparseIndex(Protocol):
    def upsert(self, records: Sequence[SparseRecord]) -> None: ...
    def search(self, request: SparseQuery) -> Sequence[RankedCandidate]: ...

class Reranker(Protocol):
    def rerank(self, request: RerankRequest) -> Sequence[RankedCandidate]: ...

class ContextBuilder(Protocol):
    def build(self, request: ContextRequest) -> ContextBundle: ...
```

`RankedCandidate` carries `chunk_id`, parent/entity IDs, rank, stage score, source blocks, content
types, document/tenant policy fields and provenance references. Backend-private objects and scores
do not cross ports. Every request has a configuration/version digest and deterministic tie-breaking
by stable chunk ID.

## 3. Query processing

Canonical query text is NFC. Optional normalization may standardize Unicode compatibility forms,
case for scripts where meaningful and safe punctuation/whitespace, but must preserve the original
query, numbers, signs, units, code and quoted text. Chinese is not lowercased or whitespace-split.
Query expansion, translation and LLM routing are outside the MVP.

Metadata filters are allow-listed typed predicates (tenant, corpus, document, language, date and
content type) and are applied consistently to dense/sparse candidates. Tenant authorization is a
mandatory prefilter, never a post-retrieval best effort.

## 4. Dense retrieval

### 4.1 Embedded representation

Embed child chunks and table row-group chunks, not giant parent sections. The exact input is:

```text
optional document title
heading path (root -> leaf)
content-type marker
derived child/table text
```

Each prefix is versioned and recorded; no provenance is invented for synthetic separators. Table
input uses caption, heading path, repeated logical headers and the row group. Figure/equation input
uses only provenance-backed caption/OCR/equation/context text. The byte-exact rendered UTF-8 input
determines `embedding_input_digest`.

### 4.2 Embedding profile

An activated profile pins:

```text
model repository/name, immutable revision/weights digest, tokenizer revision,
pooling, maximum tokens, truncation policy, vector dimension,
input prefixes, output normalization, runtime precision
```

`BAAI/bge-m3` is a multilingual candidate for the first experiment, not an architectural default.
It may be activated only after exact revision/digest and license review are recorded. The reference
profile uses L2-normalized vectors and cosine similarity; changing any profile field creates a new
index namespace.

Embeddings cache by `(tenant/policy namespace, embedding_input_digest, embedding_profile_digest)`.
Operational chunk IDs and provenance are never replaced by cache identity.

### 4.3 Reference backend

Local evaluation uses a simple exact in-memory/on-disk index behind `DenseIndex`. It must support
deterministic exhaustive cosine search and manifest validation. A production ANN/vector database is
selected later from measured corpus size, latency, filtering, durability and operational needs.

## 5. Sparse retrieval

Use a mature BM25 implementation behind `SparseIndex`. Index fields separately:

- title and heading path (stored separately; any boost is versioned);
- body/child text;
- table caption and repeated header text;
- table row-group text;
- content type and structured metadata filters.

English uses a pinned Unicode-aware word tokenizer with conservative case normalization. Chinese
uses a pinned CJK analyzer/tokenizer evaluated on the project corpus; single-character splitting is
only a comparison baseline. Bilingual text preserves both token streams. Numeric strings, currency,
units, identifiers and code are protected tokens. Stopword/stemming policy is language-specific and
versioned; no online analyzer download occurs during indexing.

## 6. Fusion and deduplication

The default hybrid strategy is rank-only Reciprocal Rank Fusion:

```text
RRF(d) = sum(1 / (k + rank_i(d)))
```

`k=60` is an initial experiment value, not a quality claim. Dense and sparse candidates are fused
by chunk ID with deterministic tie-breaks. RRF avoids assuming incomparable score calibration;
hand-tuned dense/sparse weights are not the first default.

Initial evaluation envelope (configuration, not constants): retrieve 50 dense and 50 sparse,
fuse at most 60 unique chunks, pass at most 40 to an optional reranker and retain up to 10 before
context expansion. Tune only on the retrieval development set and freeze before holdout.

Duplicate candidates are detected by exact chunk ID, embedding-input/content digest and overlapping
source-block sets. Near-duplicate semantic suppression beyond these facts is deferred until measured.

## 7. Reranking

Reranking is a distinct optional stage. A profile pins model/revision/digest/tokenizer, query/document
format, maximum length/truncation, precision and candidate/final counts. `BAAI/bge-reranker-v2-m3`
is a multilingual candidate, not preselected. Report reranker latency separately and preserve both
pre-rerank rank and final rank. If unavailable, hybrid retrieval remains a valid baseline.

## 8. Parent expansion and tables

Retrieval normally selects children/table units. After selection, each result may resolve to its
parent section/table context:

- multiple selected children of one parent are grouped and source-block overlap is removed;
- only the bounded relevant neighborhood is expanded, not the entire oversized parent;
- the highest child evidence/rank is retained and all contributing child IDs are recorded;
- parent expansion never changes the original retrieval score into an invented comparable score.

Tables are first-class candidates. Canonical `Table` remains the source of truth; the retrieval
serializer is a derived, versioned view containing caption, heading path, repeated headers and row
group. It never fabricates missing cells/geometry or replaces the table with Markdown in IR.

## 9. Context builder and citation bundle

The builder receives ranked candidates plus immutable IR/chunk manifests and enforces:

- exact tokenizer/model token budget including separators and citation markers;
- tenant/metadata policy and configurable per-source diversity;
- no duplicate source-block overlap;
- parent-child deduplication;
- coherent table header + selected row group, and complete protected atomic units;
- stable ordering by retrieval evidence, then document reading order within grouped context;
- provenance retention for every context span.

`ContextBundle` records selected/dropped candidates and reasons, rendered context spans, token count,
document/page/bbox/entity/block references, retrieval/rerank traces and a citation bundle. A citation
resolves `context span -> chunk -> entity/block -> page/bbox -> source document`. Unsupported or
bbox-less evidence is labeled at its actual provenance precision.

## 10. Failure, observability and tests

Empty results are a normal typed outcome. Missing index/profile mismatch is fatal for that query;
reranker unavailability may fall back only to a configured non-reranked policy and is disclosed.
No stage silently broadens tenant filters or truncates a table into incoherence.

Record stage latency, candidate counts, filters, duplicates, expansion/token-budget drops, actual
device and configuration digests. Do not expose query/document text in metrics labels.

Required tests cover deterministic ranks/ties, filter isolation, Chinese/English/numeric tokenization,
RRF known vectors, duplicate parent children, table header coherence, exact token budgets, citation
resolvability, cache invalidation and backend contract equivalence.

## 11. Explicit non-goals

Production vector-store selection, distributed indexing, query agents, Graph RAG, RAPTOR, LLM query
expansion and multi-agent retrieval are outside the first retrieval implementation.

## 12. Candidate primary sources

- [BGE-M3 official model card](https://huggingface.co/BAAI/bge-m3)
- [BGE reranker v2 M3 official model card](https://huggingface.co/BAAI/bge-reranker-v2-m3)

These links identify experiment candidates only. An implementation must pin immutable revisions and
record license/security approval rather than resolving a moving default branch at runtime.

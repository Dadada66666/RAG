# RAG Evaluation Specification

| Field | Value |
|---|---|
| Status | Minimal exact-dense OHR A/B path implemented |
| Contract version | `rag-eval/1.0.0` |
| Rule | Parsing, retrieval and answer quality remain separate result layers |

## 1. Purpose

This specification measures whether parsing and retrieval improvements help real RAG tasks without
hiding regressions in a single score. Every experiment uses frozen corpora, queries, relevance
judgments and exact pipeline manifests. Latency/cost are reported beside, never folded into, quality.

The current QA commands add an independent answer-evaluation layer; they do not replace or modify
the OHR retrieval A/B. Exact quotation validation checks submitted-source membership, not semantic
support. `rag-evaluate` requires independent correctness and citation-support judgments. See
[evidence QA evaluation and server validation](EVIDENCE_QA_GUIDE.md) for the implemented scope.

## 2. Dataset and split contract

A RAG evaluation item records:

```text
item_id, corpus/document IDs and digests, query, language/slice,
relevant chunk/entity/block/page IDs with graded relevance,
table/numeric flags, expected answer or deterministic assertions,
required/acceptable citation sources, unanswerable flag,
annotation/adjudication status, split and template/family group
```

Development tunes chunk/retrieval parameters. Calibration may select a policy. A protected holdout
is opened only after configuration freeze. Source-document/template families cannot cross splits.
Queries must include Chinese, English, bilingual, tables, critical numerics, multicolumn and scanned
documents, plus explicit unanswerable cases. Annotation disagreement is adjudicated and reported.

## 3. Layer A — Parsing quality

Consume immutable results from `EVALUATION_SPEC.md`:

- official ParseBench-compatible metrics where the pinned upstream protocol applies;
- project table structure and critical numeric structural accuracy;
- reading order and provenance resolvability/precision;
- Quality Gate accepted-output precision, coverage, fallback and unresolved failure rates.

These metrics diagnose ingestion. They are not averaged with retrieval or answer metrics.

## 4. Layer B — Retrieval quality

### 4.1 Required metrics

- `Recall@1`, `Recall@5`, `Recall@10` using adjudicated relevant sources;
- Mean Reciprocal Rank (MRR);
- `nDCG@k` for graded relevance;
- table retrieval recall on table queries;
- citation-source recall: whether retrieved evidence contains every required citation source;
- zero-result, wrong-tenant/filter and duplicate-context rates;
- P50/P95/P99 end-to-end and per-stage latency.

Report query macro averages with raw hit/query denominators and slice breakdowns. For multi-source
questions also report all-required-source recall. Multiple hits from the same source do not inflate
source recall.

### 4.2 Comparison design and current scope

The active QA plan is [COMPLEX_DOCUMENT_QA_SPEC.md](COMPLEX_DOCUMENT_QA_SPEC.md). Its current
experiment fixes retrieval and compares source-context construction. Stages D/E below are deferred
historical experiment options, not mandatory next work. Batch QA scores require the fixed question
manifest and include provider failures; legacy result-only scores are exploratory.

Run the same frozen query set, embedding model, dense backend and retrieval budget:

1. **A — Legacy baseline:** simple/native PDF text → fixed-token chunks → dense retrieval;
2. **B — IR-controlled baseline:** Canonical IR → deterministic flat-text rendering → the same
   fixed-token chunker → the same dense retrieval;
3. **C — Structure-aware:** Canonical IR → parent-child/structure-aware chunks → the same dense
   retrieval;
4. **D — Hybrid:** C + sparse/BM25 + Reciprocal Rank Fusion;
5. **E — Reranked:** D + pinned reranker and parent/context expansion.

Interpret deltas independently: `A → B` measures document representation, `B → C` measures
chunking, `C → D` measures hybrid retrieval, and `D → E` measures reranking/context construction.
No stage may change an earlier controlled variable without creating a separate experiment.

Publish absolute results and incremental delta for each stage. A later stage is promoted only when it
improves its declared target slices without unacceptable latency, citation or other-slice regression.
The current `rag-retrieval-ab` experiment compares the unchanged IR fixed-token control with the
relationship-bound semantic-packing v2 representation using one local BGE-M3 runtime, shared query
embeddings and exact NumPy cosine ranking. The run manifest also records embedding candidate count,
average/median structure chunk tokens, and structure Table/normal-child counts so fragmentation is
visible beside retrieval quality. OHR evaluation reports `PageHitRate@1/5/10` and MRR for ALL,
TEXT, TABLE and READING_ORDER. PageHitRate is deliberately not labeled Recall because OHR truth
here is page-level evidence rather than adjudicated chunk relevance.

Experiment contract `rag-retrieval-ab@1.1.0` first enforces per-document parity between the
renderable Retrieval Evidence View and both chunk representations. Its activation diagnostics make
ordered/unresolved/unrenderable evidence, Canonical Table blocks, Table chunk rendering mode, and
explicit caption binding visible. `TableSourceExposure@1/5/10` is a non-primary diagnostic for
TABLE queries: it reports whether Top-K contains a chunk covering a Canonical TABLE source block.
It is not TableAccuracy, TableRecall, or a substitute for the page-level primary metrics.

### 4.3 Relevance and citations

Relevance is assigned to canonical entities/source blocks, then resolved to the derived chunk/version
under evaluation. This prevents chunk-boundary changes from rewriting semantic ground truth.
Citation-source recall uses canonical provenance, not text similarity. Bbox-exact, page-only and
unresolved citations are reported separately.

## 5. Layer C — Answer quality

For a curated, adjudicated QA set report:

- answer correctness using deterministic exact/set/numeric assertions where possible;
- critical numeric exact match, including sign, precision, currency/unit and structural source;
- citation correctness: cited evidence supports the associated claim;
- citation completeness: required claims/sources have citations;
- unsupported-answer/faithfulness rate;
- abstention precision/recall for unanswerable items;
- answer-generation latency and token/cost counters separately.

An LLM judge may be one versioned secondary signal for semantic answers. It must record model,
prompt/rubric, sampling parameters, repetitions and agreement with human/deterministic labels. It
cannot override an exact numeric mismatch, missing citation or deterministic contradiction. Human
adjudication remains required for disputed promotion decisions.

## 6. Experiment manifest and reproducibility

Every report stores:

```text
benchmark ID/version, exact dataset/subset/split IDs and digests,
query/QA annotation and evaluator versions, parser/adapter/model versions,
IR/schema/pipeline/quality/fallback versions,
chunker/tokenizer/config versions, embedding model immutable revision/digest,
sparse engine/analyzer, fusion parameters, reranker revision/config,
candidate counts/top-k, parent/context/token-budget policy,
generator and judge profiles when applicable, seed, hardware/device,
dependency-lock and source commit, per-item outputs and exclusions
```

Machine JSON contains raw per-item judgments and counters. Markdown is a derived summary. Any changed
manifest field creates a distinct run ID; operational timestamps do not change the semantic result
digest.

## 7. Promotion and public reporting

- Parsing, retrieval, answer, latency and cost appear in separate report sections.
- No composite “enterprise RAG score” is allowed.
- Public wording identifies official ParseBench, ParseBench-derived subsets and Project Golden Sets
  exactly as specified in `EVALUATION_SPEC.md`.
- Resume claims are baseline-to-improved-system comparisons with corpus version, sample size,
  hardware and confidence interval where meaningful.
- No accuracy claim is valid until a real, adjudicated benchmark corpus is executed.

The Quality Gate is not a correctness oracle. `accepted-output precision >= 95%` only describes
accepted outputs in a declared supported slice and must always include coverage.

## 8. Tests and acceptance

Required evaluator tests:

- known vectors for Recall/MRR/nDCG, graded ties and no-relevant-result cases;
- table/source recall deduplicates multiple chunks from one source;
- exact numeric rules reject wrong sign/precision/location;
- citation correctness/completeness and bbox/page precision vectors;
- missing pipeline output receives a failed/zero outcome, not exclusion;
- macro aggregation and slice denominators remain stable under document-size imbalance;
- manifest drift, split leakage and protected-holdout access fail closed;
- deterministic rerun produces the same semantic report.

An experiment is valid only when all inputs resolve, evaluator tests pass, exclusions are explicit
and every compared system uses the same eligible query set and budgets.

## 9. Deferred experiments

Late Chunking may be tested only with a compatible pinned embedding model. RAPTOR is tested only if
multi-hop/document-level error analysis establishes a need. Graph RAG, agents and LLM-generated
parent summaries are not part of the first RAG evaluation.

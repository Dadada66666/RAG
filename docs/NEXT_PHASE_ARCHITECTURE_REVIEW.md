# Next-Phase Architecture Review

| Field | Value |
|---|---|
| Status | Accepted as the post-Phase-2.6 implementation audit |
| Review date | 2026-09-01 |
| Audited commit | `f1c585257bd360d25fb1521adca5b96f2bea5d15` |
| Branch | `main` (`HEAD == origin/main` when reviewed) |
| Review scope | Actual source, tests, schemas, specifications and ADR-001 through ADR-006 |

## 1. Evidence and CI status

This review does not infer implementation state from README claims. It inspected both parser
adapters, the parser-neutral contract, preflight/native evidence, the shared normalizer, Canonical
IR models and invariants, Phase 2.6 evaluation code, CLI/application composition, synthetic
contract fixtures, opt-in real-parser tests, and all governing specifications.

GitHub Actions workflow `CI` completed successfully for the exact audited commit on 2026-09-01:
[run 33517492746](https://github.com/Dadada66666/RAG/actions/runs/33517492746). The workflow runs
Ruff, mypy, generated-schema drift checking and the default offline pytest suite. The Phase 2.6
commit records 217 passing default tests and 85.93% coverage for the IR package. Real Docling and
Paddle model tests are opt-in and were not part of that default run; CI success is therefore
contract/domain evidence, not parser-accuracy evidence.

## 2. Implementation-state audit

Status meanings:

- `IMPLEMENTED`: executable production-shaped behavior exists and is covered by relevant tests.
- `PARTIALLY_IMPLEMENTED`: useful executable behavior exists, but the named capability is
  incomplete, uncalibrated, opt-in-only, or narrower than the product requirement.
- `SPEC_ONLY`: an authoritative wire/domain specification exists, but no executable capability.
- `NOT_IMPLEMENTED`: neither an executable capability nor an adequate prior implementation exists.

| Capability | Status | Repository evidence and boundary |
|---|---|---|
| Canonical IR | IMPLEMENTED | Strict Pydantic IR 1.1, generated JSON Schema, deterministic serialization/migration and whole-document graph invariants exist under `src/docparser/ir/`. |
| Docling parser | PARTIALLY_IMPLEMENTED | Pinned `docling-standard` document-scope adapter, mapping and opt-in real smoke exist. Models are optional; no real Golden baseline or selective page/table execution exists. |
| PaddleOCR-VL-1.6 parser | PARTIALLY_IMPLEMENTED | Pinned full-pipeline optional adapter, structured block/HTML-table mapping and opt-in CUDA tests exist. No executed project corpus baseline or selective scope exists. |
| Native PDF evidence | PARTIALLY_IMPLEMENTED | Page text/status/MediaBox/CropBox/rotation/image signals are retained. OCR-backed hidden text, broken-font extraction and trustworthy positional text evidence are not classified. |
| Numeric evidence | PARTIALLY_IMPLEMENTED | Conservative page token extraction, multiplicity diagnostics and disagreement facts exist. Structural table/cell correctness is not established. |
| Parser-neutral contract | IMPLEMENTED | Strict neutral models and `DocumentParser` Protocol exist; both adapters return `ParseResult`. It is intentionally limited to current document execution and does not implement all aspirational scope types. |
| Neutral normalization | PARTIALLY_IMPLEMENTED | Both adapters share one neutral-to-IR implementation, but it is incorrectly housed in `normalization/docling.py` and does not reconstruct full hierarchy/relationships/cross-page tables. |
| Table structure | PARTIALLY_IMPLEMENTED | Logical rows/columns/cells/spans and HTML rowspan/colspan mapping exist. Multiple-page identity, robust cell geometry and real complex-table accuracy remain unmeasured. |
| Cross-page tables | SPEC_ONLY | IR can represent logical tables and segments; current normalizer deliberately emits independent page-level tables unless explicit upstream evidence exists and still does not assemble them. |
| Provenance | PARTIALLY_IMPLEMENTED | IR reachability/invariants and parser block/cell records exist. Precision levels are inferred from bbox presence, raw artifacts are development snapshots, and end-to-end citation bundles do not exist. |
| Parsing benchmark | PARTIALLY_IMPLEMENTED | Manifest, runner and independent development metrics exist, but the manifest is empty and table/numeric matching has correctness defects described below. |
| Official ParseBench integration | NOT_IMPLEMENTED | No official evaluator adapter, pinned upstream protocol, fixed subset or official metric artifact exists. |
| Quality Gate | SPEC_ONLY | `NOT_EVALUATED` lifecycle exists; no rule engine, calibrated decision policy or QualityReport implementation exists. |
| Quality calibration | NOT_IMPLEMENTED | No labeled failure corpus, confusion matrices, frozen thresholds, accepted-output precision or coverage report exists. |
| Selective fallback | SPEC_ONLY | Planning/merge documents exist, but adapters truthfully support only `DOCUMENT`; no fallback executor exists. |
| Fallback merge/revalidation | SPEC_ONLY | Canonical revision/provenance primitives exist, but no candidate matcher, transactional merge or changed-scope revalidation exists. |
| Structure-aware chunking | SPEC_ONLY | `Chunk` is an implemented wire entity only. No semantic-unit builder, tokenizer packing or chunk manifest generator exists. |
| Parent-child chunking | SPEC_ONLY | Parent/child fields and invariants exist; no parent-child algorithm is implemented. |
| Embeddings | NOT_IMPLEMENTED | No embedding port, model runtime, cache or vector artifact exists. |
| Dense retrieval | NOT_IMPLEMENTED | No retrieval port or dense index implementation exists. |
| Sparse retrieval | NOT_IMPLEMENTED | No BM25/tokenization/index implementation exists. |
| Hybrid fusion | NOT_IMPLEMENTED | No fusion implementation exists. |
| Reranking | NOT_IMPLEMENTED | No reranker port/model/runtime exists. |
| Context construction | NOT_IMPLEMENTED | No budgeted context builder, parent expansion or overlap deduplication exists. |
| Citation | NOT_IMPLEMENTED | Parsing provenance is a prerequisite, but no retrieval citation bundle or answer citation validation exists. |
| RAG evaluation | NOT_IMPLEMENTED | Parsing metrics are not retrieval/answer metrics; no query/answer/citation dataset or evaluator exists. |
| API/service layer | SPEC_ONLY | API/job/storage specifications exist; no FastAPI service, durable job repository or worker scheduler is implemented. |

Wire entities are not counted as implemented algorithms. In particular, `DocumentIR.chunks`,
`Chunk.parent_chunk_id`, cross-page table segments and `QualitySummary` do not prove chunking,
cross-page reconstruction or quality validation exists.

## 3. Phase 2.6 evaluation correctness review

### MUST FIX BEFORE BENCHMARK

| Finding | Current behavior | Required correction |
|---|---|---|
| Multiple tables on one page | Truth and predictions are flattened; cells are keyed globally by `(row, column)`, so separate tables overwrite each other. | Give every truth table a stable truth ID/page span and match tables first; scope cell keys by matched table. |
| Parser table reordering | `zip(truth_tables, actual_tables)` assumes identical order. | Use deterministic type/page/geometry/content-compatible bipartite assignment owned by the benchmark, independent of production merge logic. |
| Missing and extra tables | Missing tables affect some denominators, but extra tables are mostly reported as a count and not penalized in structure accuracy. Failed parser cases disappear from slice averages. | Report table detection precision/recall/F1; unmatched truth and prediction must contribute explicit false negatives/positives. Failed parser output remains a scored failed case plus a separate infrastructure failure fact. |
| Merged cells | Span equality is checked only after unsafe position lookup and therefore can compare cells from another table. | Score span/occupancy after table and cell assignment; include occupied-grid topology and unmatched spanning-cell penalties. |
| Cross-page tables | Only `segments[0].page_number` is used; logical identity and subsequent segments are ignored. | Annotate logical table ID and ordered page segments; score continuation identity, segment coverage and logical rows independently. |
| Duplicate cell coordinates | A single dictionary entry wins for identical `(row, column)` across tables. | Use `(truth_table_id, row, column)` after table assignment; duplicate occupancy inside one table is invalid, across tables is normal. |
| Critical numeric location | Optional row/column fields are ignored; a value anywhere on the page counts as correct. | Require structural lookup through matched table/cell when structural truth is supplied; report page-presence and structural correctness as different metrics. |
| Numeric multiplicity | A set removes duplicate values. | Use multisets for page token presence and exact cell instances for structural truth. |
| Macro/micro averaging | Per-page means and per-document means are averaged without exposing denominators; documents with different annotated cells/pages get equal or accidental weight. | Emit raw counts and both micro and macro-by-document aggregates. Slice reports disclose documents/pages/tables/cells/queries contributing to each metric. |
| Missing parser output | Exceptions become failure rows but the parser is absent from slice metric averages, biasing results upward. | Represent an expected case with `output_status`; completeness/coverage are zero for missing output and all quality metrics expose scored/eligible counts. Infrastructure failure is reported separately, never silently omitted. |
| Long-page edit distance | Pure Python dynamic programming is `O(n*m)` time and can make long pages impractical. | Use a pinned optimized evaluator with a declared maximum input budget/timeout. An incomplete metric fails the benchmark completeness gate; it is never converted to a favorable score. |

Reading-order truth matching also selects the first same-type/text block and ignores geometry, so
duplicate paragraphs can be matched incorrectly. Text assembly does not explicitly define whether
table-cell text belongs in page text truth. Both policies must be versioned before a baseline run.

### MUST FIX BEFORE QUALITY GATE

- Create a real, versioned development corpus and a protected holdout; the committed empty manifest
  is only runner readiness.
- Label actual parser defects and define the supported-slice correctness predicate used to
  determine whether output is acceptable.
- Separate native-text source class (`born-digital`, OCR-backed hidden layer, broken extraction,
  image-only) before calibrating native/parser disagreement rules.
- Calibrate every heuristic signal using precision, recall, false-positive and false-negative rates.
- Resolve the existing IR requirement that evaluated `QualitySummary.score` is non-null when the
  first gate intentionally uses a discrete decision without a calibrated continuous score. The
  smallest compatible solution is a V1 minor relaxation allowing `score=null` for evaluated
  decisions while keeping `quality_report_id` required; ADR-006 and migration/schema tests must be
  amended in that implementation increment.

### CAN DEFER

- Learned/ML or LLM quality rules.
- Chart semantic extraction metrics beyond the official ParseBench protocol.
- Cross-page geometry-only table reconstruction.
- Complex chunk-boundary optimization, Late Chunking and RAPTOR.
- Production vector database, distributed retrieval and service APIs.

## 4. Evaluation terminology policy

The following labels are mutually exclusive:

1. **Official ParseBench metric** — produced by a pinned, unmodified compatible official
   ParseBench evaluation protocol over the declared official dataset/subset. The project exports
   predictions into the official representation and does not reimplement the named evaluator.
2. **ParseBench-derived development subset** — a fixed, versioned list of ParseBench case IDs,
   evaluated with an explicitly named protocol. If the official evaluator is used, name the
   official dimension and subset; if project metrics are used, call them project metrics over a
   ParseBench-derived subset.
3. **Project Golden Dataset metric** — produced by this repository's annotation schema and metric
   implementation. It is never called a ParseBench score.

README, benchmark reports, release notes and resume claims must include the exact category. For
example:

```text
Official ParseBench: Table GTRM = X
ParseBench-derived parsebench-complex-v1-dev: official Table GTRM = X
Project Financial/Chinese Golden Set: critical numeric structural exact accuracy = X
```

The official project defines five separate dimensions and uses GTRM for tables; its own repository
states that the dataset contains roughly 2,000 enterprise pages and that the evaluator is
deterministic/rule-based. Integration therefore preserves the upstream evaluator rather than
copying its formulas: [official ParseBench repository](https://github.com/run-llama/ParseBench),
[paper](https://arxiv.org/abs/2604.08538).

## 5. Architecture corrections adopted

1. Move behavior-preservingly shared neutral normalization from `normalization/docling.py` to
   `normalization/neutral.py`; Docling/Paddle modules remain compatibility entrypoints only.
2. Repair project benchmark matching before any parser baseline is published.
3. Integrate ParseBench by pinned adapter/export and upstream evaluator reuse, not by importing its
   metric names into project-local code.
4. Replace uncalibrated score/weight-first Quality Gate behavior with a calibrated discrete risk
   decision: `ACCEPT`, `FALLBACK_REQUIRED`, `REJECT`.
5. Block automatic fallback until failure detection and target-scope precision are demonstrated.
6. Treat chunking as an experiment against fixed-token baseline; chunk wire models remain only a
   contract until an algorithm produces them.
7. Keep retrieval behind local-evaluable ports and make dense, sparse, fusion, rerank, context and
   citations separately measurable stages.

No new ADR is added by this review. Existing ADR-001/002/003/006 already own Canonical IR, parser
boundaries, quality-gated fallback and versioning. The planned nullable evaluated quality score is
a public wire compatibility relaxation and therefore requires an ADR-006 compatibility amendment
when implemented; it is not silently changed in this design-only task.

## 6. Stop/go verdict

The repository is ready for **benchmark correctness and official ParseBench integration**, not for
automatic Quality Gate acceptance, fallback, chunking or retrieval. No accuracy claim is valid
until a real benchmark corpus is executed. Quality Gate is a calibrated risk gate, not a
correctness oracle. Native PDF text is independent evidence, not ground truth.

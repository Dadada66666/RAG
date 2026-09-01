# Evaluation and Golden Dataset Specification

| Field | Value |
|---|---|
| Status | Proposed |
| Benchmark contract | `benchmark/1.0.0` |
| Purpose | Quality, reliability and efficiency comparison with protected regression gates |

## Phase 2.6 development implementation note (2026-09-01)

The repository now includes a small local-only `GoldenDatasetManifest` and annotation contract for page text, layout blocks, pairwise reading order, logical tables/cells/spans and critical numerics. The committed manifest contains no copyrighted PDF and targets 20–50 evaluation-approved difficult pages. An empty manifest proves only that the runner is ready; it provides no accuracy evidence.

`docparser benchmark-parsing` runs `docling-standard` and `paddleocr-vl-1.6` on the same enabled sources and emits deterministic JSON plus a concise Markdown comparison. It reports page completeness, normalized edit similarity, pairwise order accuracy, table detection and logical row/column/cell/span accuracy, critical numeric exactness, provenance precision/completeness, elapsed time, pages/sec and actual device independently. It intentionally produces no universal parser score. Promotion remains blocked until the local corpus is supplied and slice results are reviewed.

## 1. Questions the system must answer

1. Which parser/pipeline is better for each protected document slice, not just on average?
2. Does a candidate improve text while degrading tables, reading order, provenance or latency?
3. Does selective fallback repair the intended scopes with less work and no boundary regressions?
4. Did a schema/rule/chunker/model change silently change accepted behavior?
5. Are results reproducible enough to promote and roll back on the reference deployment?

There is no universal “Parser A wins” scalar. Promotion is a constrained Pareto decision over blocking quality gates, slice deltas, reliability, throughput, VRAM and license/security eligibility.

## 2. Golden Dataset organization

```text
tests/golden/
├── manifest.schema.json
├── manifest.yaml
├── annual_reports/
├── scientific_papers/
├── scanned_docs/
├── invoices/
├── financial_tables/
├── merged_cells/
├── multi_page_tables/
├── two_column/
├── chinese/
├── bilingual/
├── rotated/
├── noisy_scan/
├── figures_captions/
├── formulas/
└── adversarial_security/
```

Large/licensed PDFs and annotations may live in a controlled artifact store. Git contains manifests, tiny redistributable fixtures, annotation checksums and acquisition/license records.

### 2.1 Dataset splits

- `development`: visible; used to build adapters/rules.
- `calibration`: visible labels but excluded from implementation unit fixtures; used to tune thresholds/confidence.
- `protected_test`: labels sealed from routine tuning; CI/release runner reports final gates.
- `security`: malicious/corrupt fixtures isolated from normal quality aggregates.

Document families—not pages—are assigned to one split to prevent template leakage. Near-duplicate/template hashes are checked across splits.

### 2.2 Manifest entry

Required fields:

```yaml
document_key: annual-report-zh-001
source_artifact_id: golden_art_...
source_sha256: sha256:...
license: internal-evaluation-approved
split: protected_test
slices: [annual_reports, chinese, born_digital, financial_tables]
page_count: 186
language: [zh-Hans, en]
annotations:
  text: golden_ann_text_...
  blocks: golden_ann_blocks_...
  reading_order: golden_ann_order_...
  tables: golden_ann_tables_...
  structure: golden_ann_structure_...
  provenance: golden_ann_prov_...
  chunks: golden_ann_chunks_...
annotation_schema_version: "1.0.0"
annotation_guideline_version: "1.0.0"
adjudication_status: adjudicated
```

Also record source provenance, privacy classification, retention, annotator/adjudicator roles (pseudonymous IDs), known ambiguity and excluded metrics.

## 3. Annotation model and quality

- Annotate in canonical top-left point coordinates and retain source transform.
- Text truth preserves reading-order text, line/paragraph boundaries and Unicode normalization policy.
- Layout truth includes block type, bbox/polygon, containment and ignored/ambiguous regions.
- Table truth includes logical grid, cell spans/text/header roles and page segments/continuity.
- Structure truth includes heading levels, section ranges, captions, footnotes and equation links.
- Chunk truth marks acceptable boundary ranges/semantic units rather than one brittle exact packing where multiple answers are valid.

At least 10% of protected samples per major slice are double-annotated. Report inter-annotator agreement. Low-agreement items are adjudicated or marked ambiguous and excluded from inappropriate exact gates while remaining available for qualitative review.

Annotation guidelines and schemas are versioned. Annotation fixes create a new dataset version; historical benchmark reports remain tied to the old digest.

## 4. Quality metrics

All metrics are reported micro, macro-by-document and by slice. Confidence intervals use document-level stratified bootstrap to avoid treating pages from one document as independent.

### 4.1 Text

- **CER:** Unicode grapheme/character edit distance divided by reference characters; report overall and zh/en/mixed slices.
- **WER:** language-aware word segmentation; Chinese WER is reported only with a pinned segmenter, never substituted for CER.
- **Normalized edit similarity:** secondary, with normalization policy disclosed.
- Missing/duplicated text rate and unsupported-character/replacement rate.

Header/footer inclusion policy is consistent between truth and prediction. Both raw-flow and body-only scores may be reported.

### 4.2 Layout and classification

- Block detection AP/mAP at declared IoU thresholds per canonical block type.
- Precision/recall/F1 after deterministic type-compatible matching.
- Mean/median bbox IoU and boundary error in points.
- Caption-to-figure/table relationship F1.
- Header/footer/page-number classification F1.

### 4.3 Reading order

- Pairwise order accuracy on comparable flow-block pairs.
- Kendall tau/edit distance on matched order sequences.
- Column-transition error and cycle/disconnected-order rate.

Unmatched blocks contribute through missing/extra penalties rather than disappearing from the order metric.

### 4.4 Tables

- Table detection precision/recall and bbox IoU.
- Structure similarity using TEDS and/or GriTS-style topology/location/content measures with exact implementation/version pinned.
- Cell detection/matching precision/recall/F1.
- Cell text CER, row/column/spanning-cell accuracy and occupied-grid validity.
- Cross-page continuation precision/recall, repeated-header handling and logical-row coverage.
- Financial-table critical-cell exact accuracy on annotated numeric cells (format-normalized and raw variants).

### 4.5 Document structure

- Heading classification/level accuracy.
- Section boundary precision/recall and tree-edit or parent-edge F1.
- List hierarchy, footnote link, equation extraction/link and reference-entry F1 where annotated.
- Page completeness and duplicate-page/block rate.

### 4.6 Provenance

- Entity provenance completeness: percentage with resolvable chain to source artifact.
- Correct page accuracy and bbox IoU/coverage against truth.
- Parser/version/model attribution completeness.
- Chunk citation resolution success and highlight coverage.
- Out-of-scope fallback provenance rate; target is exactly zero.

Any broken source chain or missing accepted page is a release-blocking invariant, independent of average score.

### 4.7 RAG readiness

- Semantic boundary precision/recall against acceptable boundary windows.
- Orphan heading/caption/equation/table rate.
- Chunk semantic continuity and context sufficiency on adjudicated rubric.
- Token-limit violation and table row/header coverage.
- Parent-child consistency and source-block coverage/duplication.
- Retrieval proxy: fixed query set with Recall@k/nDCG/MRR and citation correctness, reported as downstream evidence—not used to hide parse-quality regressions.

## 5. Fallback-specific evaluation

Compare primary-only baseline, primary+validator without repair, and primary+selective fallback:

- issue detection precision/recall by rule on annotated defects;
- fallback target scope IoU/coverage and unnecessary-area ratio;
- fallback page/area/call rate;
- trigger-to-repair success rate by issue/parser/slice;
- quality delta on target and non-target boundary regions;
- duplicate/conflict/rejected candidate rate;
- whole-document fallback rate;
- extra latency/GPU-seconds/storage bytes per repaired issue;
- false fallback rate on primary PASS documents.

A fallback configuration fails promotion if it improves target score while causing a protected non-target regression beyond tolerance.

## 6. Reliability evaluation

Fault scenarios:

- parser timeout/crash/invalid output;
- GPU OOM/unavailable and worker recycle;
- API/scheduler/worker process kill at each checkpoint boundary;
- disk full/slow, checksum mismatch and SQLite lock contention;
- corrupt/encrypted/huge-dimension PDFs;
- cancellation races and duplicate/idempotent submissions;
- stale merge lease/concurrent retry;
- artifact write succeeds but metadata commit fails, and inverse.

Metrics: terminal correctness, data corruption count, checkpoint replay units, recovery time, retry count, leaked scratch/unreachable artifacts, state-transition legality and audit/telemetry completeness.

## 7. Performance protocol

### 7.1 Reproducibility manifest

Every run records:

- source/annotation/benchmark-runner digests;
- OS/kernel, CPU/RAM, GPU model/driver/CUDA, disk/storage backend;
- container image, Python/dependency lock and parser/adapter/model digests;
- config, renderer/DPI, batch/concurrency, precision and warm/cold mode;
- ambient load and run timestamps.

Network model downloads are disabled. GPU clocks/power policy are recorded when controlled.

### 7.2 Procedure

- Separate cold start/model-load from warm steady-state.
- Warm up before measured iterations.
- Randomize/interleave candidate order to reduce thermal/cache bias.
- Run enough document-level repetitions for stable intervals; never multiply pages as independent repetitions.
- Report median, P50/P95/P99 latency, pages/sec, documents/hour, CPU/RAM, GPU utilization/peak VRAM, rendered bytes, storage I/O and estimated cost/page.
- Report results by page-count/document-class bucket and concurrency.
- Verify outputs/quality during performance runs; throughput of failed/low-quality output is not success.

## 8. Regression gates

Thresholds below are initial proposals and must be calibrated before V1. A protected invariant failure always blocks.

| Metric | Initial gate |
|---|---|
| Page completeness, provenance resolution | No regression; must remain 100% on valid protected fixtures |
| Text CER | No slice worsens by > 0.5 absolute percentage point or > 5% relative without approved trade-off |
| Reading-order pair accuracy | No protected slice worsens by > 1.0 point |
| Table structure/cell F1 | No table slice worsens by > 1.0 point; critical numeric cells no regression |
| Heading/section parent-edge F1 | No protected slice worsens by > 1.0 point |
| Chunk provenance/token hard limits | Zero violations |
| P95 latency | <= 10% regression unless a documented blocking quality improvement is approved |
| Peak RAM/VRAM | Must remain under deployment budget; >10% increase requires capacity review |
| Failure/whole-document fallback rate | No statistically/materially worse protected slice |

Gate logic uses paired per-document deltas and bootstrap confidence intervals. Small sample slices require exact review rather than false statistical certainty. Waivers require an ADR/amendment with reason, impacted slices, expiration and rollback.

## 9. Benchmark runner

```bash
docparser benchmark \
  --suite tests/golden/manifest.yaml \
  --candidate docling-primary-paddle-fallback \
  --baseline release-1.0 \
  --hardware-profile single-gpu \
  --output ./benchmark-results
```

Runner stages:

1. Validate dataset/license/access and reproducibility manifest.
2. Resolve pinned images/models/configs offline.
3. Execute candidates in isolated jobs with randomized order.
4. Validate all produced IR before scoring.
5. Match entities using benchmark-owned algorithms, not production merge logic alone.
6. Calculate per-document/slice aggregates and confidence intervals.
7. Generate machine JSON, CSV/Parquet details, Markdown summary and visual overlays/diffs.
8. Evaluate gates and sign/store immutable report.

Exit codes: `0` pass, `2` invalid benchmark/config, `3` completed with warnings, `4` regression gate failed, `5` infrastructure/incomplete run.

## 10. Report structure

```text
Executive gate result
Reproducibility and license manifest
Dataset coverage/slices/exclusions
Quality metrics with paired deltas and confidence intervals
Fallback effectiveness and boundary effects
Reliability/fault-injection results
Latency/throughput/RAM/VRAM/storage/cost
Top regressions and improvements with page overlays
Unscored failures and reasons
Promotion recommendation / required follow-ups
Machine-readable artifact links and digests
```

Visual artifacts show reference/predicted bboxes/order/table grids without exposing protected documents outside authorized storage.

## 11. Answering “Parser A or Parser B?”

Decision procedure:

1. Reject any candidate failing license/security, contract or hard integrity gates.
2. Compare paired quality deltas by high-priority slice and use case.
3. Check complementarity: a fallback should win specifically on defects the primary loses.
4. Evaluate reliability, resource budget and operational complexity.
5. Select a default only if it is non-inferior on protected critical slices and fits deployment constraints; otherwise use capability/profile routing or retain baseline.
6. Record decision, pinned versions, known losing slices, fallback policy and rollback in ADR.

Vendor/public benchmark claims may nominate candidates but cannot promote them. The project Golden Dataset is authoritative for this workload.

## 12. Dataset governance and tests

- Review licensing/privacy before adding a document; do not commit proprietary PDFs casually.
- Dataset releases are immutable and checksummed.
- Annotation schema/guideline migration is tested and does not silently rewrite truth.
- Benchmark metric implementations have unit/golden tests against known examples.
- Leakage/near-duplicate checks run on every dataset change.
- CI runs a tiny smoke subset; nightly/release runs protected GPU/full suites.
- Flaky infrastructure re-runs are distinguished from quality failures and cannot be used to cherry-pick favorable results.

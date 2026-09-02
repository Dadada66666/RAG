# Parsing Evaluation and Golden Dataset Specification

| Field | Value |
|---|---|
| Status | Authoritative next-phase contract; current Phase 2.6 evaluator is development-only |
| Contract version | `parsing-eval/2.0.0` |
| Scope | Parser accuracy, provenance, Quality Gate and fallback evaluation |

## 1. Purpose and terminology

This specification answers, with reproducible evidence, which parser or routing policy is better
for a declared document slice. A valid Canonical IR only proves contract integrity; it does not
prove extraction correctness.

The following names are disjoint:

1. **Official ParseBench metric**: a result produced by the pinned official evaluator using its
   compatible protocol and named exactly as upstream defines it.
2. **ParseBench-derived development subset**: a fixed, versioned subset of ParseBench documents,
   evaluated with an explicitly named official or project protocol. It is never called an official
   full-dataset ParseBench score.
3. **Project Golden Dataset metric**: a result from the project annotation schema and project-owned
   metric implementation.

README, reports, CI and public claims must use one of these labels verbatim. Project table metrics
must never be renamed to ParseBench GTRM or any other upstream metric.

## 2. Current evaluator status and required corrections

The Phase 2.6 benchmark runner, schemas and metrics are **PARTIALLY_IMPLEMENTED development
scaffolding**. They must not be used for parser promotion, quality thresholds or public claims until
these defects are fixed and regression-tested:

| Defect | Required correction |
|---|---|
| Truth and predicted tables are flattened | Preserve `(document, page, table_id)` identity throughout scoring |
| Tables are paired with `zip`/parser order | Perform deterministic one-to-one table matching before cell scoring |
| Cells are keyed only by `(row, column)` | Key by matched table identity plus logical coordinates |
| Missing/extra tables are not fully penalized | Emit unmatched truth/prediction counts and zero-credit denominators |
| Merged cells are position-matched before span checks | Match table first, then logical anchors; score text and spans separately |
| Cross-page tables use one segment/page | Preserve logical table ID and all page segments; score continuation separately |
| Critical numbers use page-wide set membership | Preserve multiplicity and optional table/row/column identity |
| Failed/missing parser output disappears from means | Include every expected sample with explicit zero/failed outcome |
| Macro/micro aggregation is ambiguous | Publish raw counts, document/page macro and cell/token micro results separately |
| Long text edit distance is unbounded `O(n*m)` | Use bounded/page-segmented implementation with recorded truncation/timeout policy |
| Reading-order matching can select the first same text/type | Use annotated stable IDs or deterministic geometry/text assignment |
| Text assembly policy is implicit | Version the exact included entities, order and Unicode normalization |

No parser comparison is valid while the committed project Golden manifest has no adjudicated
corpus. `NO ACCURACY CLAIM` is the required report status in that condition.

## 3. Dataset architecture

```text
tests/golden/
  manifests/
    project-development-v1.json
    project-calibration-v1.json
    project-holdout-v1.json       # identifiers/digest protected from tuning
    parsebench-complex-v1-dev.json
  annotations/
  approved-synthetic/
```

PDF binaries that are copyrighted, proprietary or license-incompatible are not committed. A
manifest may reference locally provisioned approved files by logical ID and SHA-256, never by an
absolute host path.

Required slices include born-digital, image-only scan, OCR-layer scan, Chinese, English, bilingual,
multicolumn, 90/270-degree rotation, differing CropBox/MediaBox, simple/merged/financial/cross-page
tables and noisy scans. Every item records document-family/template group so related pages cannot
leak across development, calibration and holdout.

### 3.1 `parsebench-complex-v1`

The first development subset is a versioned manifest of approximately 60 unique difficult pages
selected from the pinned ParseBench dataset release. Selection is deterministic, made before parser
results are inspected and records:

- upstream dataset revision and license;
- fixed seed, sorted candidate IDs and selection script version;
- selected IDs and manifest digest;
- declared strata: hard/merged tables, OCR, multicolumn/layout and difficult numeric pages where
  available;
- no duplicate source page or document-family leakage.

A separate protected holdout (initially about 20 eligible pages, subject to statistical adequacy)
is selected from the remaining pool before Quality Gate calibration. Its outcomes are not inspected
until thresholds/policy are frozen. The full ParseBench dataset is not required for each local
iteration.

## 4. Official ParseBench integration

Public comparability uses the pinned official ParseBench evaluator instead of reimplementing its
formulas. The repository adds a stable export adapter:

```text
Canonical IR / parser prediction
  -> ParseBenchExportAdapter
  -> pinned upstream InferenceResult/layout representation
  -> official evaluator at pinned commit
  -> immutable upstream result artifact
```

The integration contract records upstream repository/commit, dataset revision, evaluator command,
container/environment digest, export adapter version and unsupported mappings. Golden contract
fixtures validate export shape; a small upstream-compatible smoke validates the adapter. Upstream
metric names, including table metrics such as GTRM, are copied only from the official result.

Project-native evaluation remains responsible for requirements ParseBench does not cover:
Canonical provenance, cross-page logical table identity, structural critical financial numerics,
Quality Gate detection, accepted-output precision/coverage and downstream RAG evaluation.

## 5. Project annotation contract

Each annotation has `schema_version`, `dataset_item_id`, `source_digest`, page dimensions/rotation,
annotator/adjudication state and supported-slice labels.

### Text and layout

- expected page text or critical Unicode code-point spans;
- block truth: stable annotation ID, type, text and optional canonical bbox;
- explicit ignore regions/reasons;
- text comparison profile (NFC, line/space policy) version.

### Reading order

- stable ordered block IDs, or pair constraints `(before_id, after_id)`;
- ignored/decorative blocks explicitly marked;
- unknown truth remains unscored, never treated as correct.

### Tables

```text
truth_table_id, logical_table_id?, page_segments[], caption?,
row_count, column_count,
cells[]: cell_id, row, column, row_span, column_span,
         text, header_status, page, bbox?, critical_numeric_refs[]
```

Logical coordinates are scoped to a table. Cross-page truth identifies every segment and whether
continuation identity is adjudicated. Unknown cell geometry remains null.

### Critical numerics

```text
numeric_id, exact_value, normalized_value, page,
table_id?, row?, column?, cell_id?, currency?, unit?, multiplicity
```

Page-only truth measures presence. Table/cell truth measures structural correctness; a value found
elsewhere on the page earns no structural credit.

## 6. Project metric protocol

All metrics publish numerators, denominators, exclusions and `N/A` for undefined denominators.
Report document/page macro averages beside token/cell micro aggregates; never average only
successful outputs.

### 6.1 Page and text

- page completeness: expected pages with output / expected pages;
- CER and normalized edit similarity using the versioned text assembly profile;
- missing parser result counts as missing output, not an excluded sample;
- long pages are divided only at annotated/canonical boundaries; any computational cap is reported.

### 6.2 Reading order

Pairwise order accuracy uses adjudicated pair constraints after one-to-one predicted/truth block
matching. Missing members produce incorrect pairs; extra predicted blocks are separately reported.

### 6.3 Table matching and metrics

1. Build candidate truth/prediction table pairs on the same allowed page/logical segment using bbox
   overlap when available plus caption/header/cell-text evidence.
2. Compute a versioned compatibility score without using parser list order.
3. Solve deterministic maximum-weight one-to-one assignment with stable tie-breaks.
4. Apply a calibrated minimum compatibility; remaining tables are unmatched.
5. Score cells only inside each matched pair using table-scoped logical anchors.

Report:

- table detection precision/recall/count error;
- logical row/column exact accuracy and absolute error;
- cell exact-text accuracy (micro and per-table macro);
- rowspan/colspan exact accuracy;
- unmatched truth/prediction tables and cells;
- cross-page logical identity/segment accuracy where annotated.

Unmatched truth tables/cells contribute zero-credit expected denominators. Extra predictions affect
precision and are never ignored. Duplicate logical coordinates across different tables are valid;
duplicates inside one logical table are an invariant failure unless explained by spans.

### 6.4 Critical numeric metrics

- page token multiset precision/recall/exact overlap;
- critical numeric exact-match accuracy;
- structural numeric exact accuracy for annotated table/cell identities;
- wrong-location count, even when the same token exists elsewhere on the page.

Normalization is conservative, versioned and preserves multiplicity, sign, decimal precision,
currency and unit evidence. Ambiguous locale separators are not guessed.

### 6.5 Provenance and performance

- resolvable block/table/cell provenance;
- exact-region, parent-region and page-only precision counts;
- elapsed seconds, pages/second, peak memory when available and actual device.

Latency is reported independently from quality.

## 7. Quality Gate and fallback evaluation

For each rule and defect type report `TP/FP/TN/FN`, detection precision/recall, false-positive and
false-negative rates. At system level report accepted-output precision **with coverage**, fallback
rate and unresolved failure rate. Supported-slice definition and confidence intervals accompany
every result.

Fallback evaluation compares baseline, candidate and committed revision on the exact target and
boundary context. Report target precision, quality delta by metric, collateral regression, cost and
revalidation outcome. A fallback that does not demonstrably improve the calibrated acceptance
predicate is not committed.

## 8. Reproducibility and public metric integrity

Every run artifact records:

```text
benchmark_id/version, dataset/subset IDs and digests, annotation version,
parser/adapter/model/config versions, Canonical IR/schema/pipeline versions,
metric and export-adapter versions, seed, hardware/device,
OS/runtime/dependency-lock digest, per-item outcomes, elapsed time
```

Later RAG reports also record chunker/tokenizer, embedding model/revision, sparse analyzer,
fusion/reranker and retrieval/context configuration. Resume/public claims are baseline-to-improved
comparisons with exact denominators. Allowed wording examples:

- `Official ParseBench: <upstream metric> = X` (official evaluator/protocol only);
- `ParseBench-derived Complex-60: project table structural accuracy = X`;
- `Project Financial/Chinese Golden Set: critical numeric structural exact accuracy = X`.

## 9. Promotion and regression policy

1. Establish Docling and Paddle baselines on the frozen development manifest.
2. Correct metrics and inspect per-item error artifacts before any aggregate comparison.
3. Calibrate thresholds only on development/calibration splits.
4. Freeze parser/profile/policy versions before protected holdout evaluation.
5. Promote per declared slice only when critical metrics meet thresholds without material latency,
   provenance or unsupported-slice regression.

No single global parser score is produced. Text, tables, order, numerics, provenance and latency are
reported independently. Insufficient sample size results in `INSUFFICIENT EVIDENCE`, not promotion.

## 10. Tests

- unit vectors for multi-table reordering, missing/extra tables, merged spans, duplicate coordinates
  in separate tables, cross-page segments and wrong-location numerics;
- aggregation tests with unequal document sizes, failures and macro/micro denominators;
- property tests for table assignment permutation invariance;
- export contract tests against pinned ParseBench representation;
- dataset manifest digest, split leakage and protected-holdout access checks;
- deterministic reruns produce byte-identical machine reports except declared operational fields.

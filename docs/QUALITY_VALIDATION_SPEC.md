# Quality Validation Specification

| Field | Value |
|---|---|
| Status | Proposed |
| Ruleset version | `1.0.0` |
| Core policy | Deterministic/heuristic/statistical first; no required LLM |

## 1. Semantics

Parser execution and parse quality are independent facts:

```text
ParseResult.status == COMPLETE
```

means the adapter returned syntactically complete output for the requested scope. It does **not** imply:

```text
QualityReport.status == PASS
```

The validator consumes Canonical IR, `DocumentProfile`, source/render evidence and parser-run metadata. It produces issues, dimension scores, a publication decision and repair recommendations. It never silently edits IR.

## 2. Interfaces

```python
class QualityRule(Protocol):
    @property
    def descriptor(self) -> RuleDescriptor: ...

    def evaluate(self, context: ValidationContext) -> list[QualityIssue]: ...

class QualityValidator(Protocol):
    def validate(self, request: ValidationRequest) -> QualityReport: ...
```

`RuleDescriptor` declares:

- stable `rule_id`, version and description;
- dimension and supported scopes;
- evidence required (IR only, preflight, raster statistics, source text count);
- applicability predicate;
- deterministic flag;
- default severity/impact calculation;
- fallback mappings and suppression/deduplication key;
- computational budget.

Rules are registered by configuration/entry point. Application code does not enumerate parser-specific rule sets. Parser-specific facts may be evaluated only through generic evidence fields or namespaced rule plug-ins that emit canonical issues.

## 3. Core types

### 3.1 Enums

```text
QualityDimension:
  COMPLETENESS, TEXT, LAYOUT, TABLE, STRUCTURE, PROVENANCE

Severity:
  INFO, WARNING, ERROR, CRITICAL

QualityStatus:
  NOT_EVALUATED, PASS, DEGRADED, FAIL

FallbackUrgency:
  OPTIONAL, RECOMMENDED, REQUIRED

IssueDisposition:
  OPEN, REPAIRED, ACCEPTED_BY_POLICY, NOT_APPLICABLE, SUPERSEDED
```

`CRITICAL` is reserved for integrity/publication hard gates, not “very bad-looking” content. An issue may be an `ERROR` and highly repairable without being critical.

`NOT_EVALUATED` is the required lifecycle state after normalization and before this validator runs. It is not an evaluated outcome: `score=null`, `quality_report_id=null`, and `publishable=false`. Only the Quality Validator may transition the summary to `PASS`, `DEGRADED`, or `FAIL`, at which point both score and report ID are required. Parser success never performs this transition.

### 3.2 `QualityIssue`

Required fields:

| Field | Meaning |
|---|---|
| `issue_id` | Deterministic ID from report input revision + rule + scope + evidence key |
| `rule_id`, `rule_version` | Producing rule |
| `type` | Stable machine code, e.g. `TABLE.IMPOSSIBLE_SPAN` |
| `dimension`, `severity` | Classification |
| `message_safe` | Human-readable, no extracted text by default |
| `scope` | Document/page/region/table/figure/block plus IDs/bbox |
| `evidence` | Bounded metrics/references, not raw page text |
| `impact` | Normalized `[0,1]` document-scope penalty contribution |
| `confidence` | Rule certainty `[0,1]`; not parser confidence |
| `repairable` | Whether fallback can plausibly repair it |
| `candidate_capabilities` | Required parser capabilities |
| `disposition` | Lifecycle in this report/revalidation lineage |
| `provenance_ids` | Evidence provenance |

`impact` is calculated by rule-specific severity and affected-scope coverage. The aggregator does not guess impact from issue count.

### 3.3 `QualityReport`

```text
QualityReport
├── quality_report_id, report_version, ruleset_version
├── document_id, ir_revision_id
├── validation_scope, started_at, ended_at
├── dimension_scores{}
├── quality_score
├── status
├── publishable
├── hard_gate_failures[]
├── issues[]
├── fallback_recommendations[]
├── fallback_required
├── evaluated_rules[], skipped_rules[]
├── thresholds
└── evidence_artifact_ids[]
```

Skipped applicable rules are explicit. A report with a skipped mandatory rule cannot be `PASS`.

### 3.4 `FallbackRecommendation`

Required: `recommendation_id`, `issue_ids`, `target_scope`, `required_capabilities`, `urgency`, `preferred_strategy`, `estimated_gain`, `estimated_cost`, `boundary_context`, `constraints`, `reason_codes`. It recommends capability and scope, not a hard-coded parser name.

## 4. Rule execution model

Rules run in ordered groups:

1. **Schema/integrity hard gates:** cardinality, references, coordinates, IDs, provenance reachability.
2. **Completeness:** compare preflight/page/source signals with IR.
3. **Local content/layout/table rules:** parallel by page where safe.
4. **Cross-page/document structure:** duplicates, repeated margins, cross-page tables, heading tree.
5. **Aggregation and recommendation coalescing.**

Each rule receives an immutable context and deterministic config. Results are sorted by `(page, bbox, rule_id, issue_id)` before hashing. Timeouts of optional rules produce `skipped_rules`; mandatory-rule timeout fails validation.

Rules must declare complexity and operation/evidence budgets. Spatial rules use an R-tree/sweep-line/grid index over page-local bboxes; text duplicate rules use bounded fingerprints/inverted indexes before pair scoring. Unbounded all-pairs block/cell comparisons are prohibited. Exceeding a mandatory-rule budget produces an explicit validation failure or conservative degraded result according to the rule descriptor; it never silently skips evidence. Metrics expose candidate-pair counts and budget exhaustion.

Revalidation after merge executes:

- all rules whose evidence intersects changed entities/pages;
- neighbor-page/cross-page rules declared by dependencies;
- all document hard invariants;
- score aggregation across retained unchanged results and new results.

## 5. Rule catalog for ruleset 1.0

### 5.1 Completeness/document

| Rule code | Detection | Default outcome | Repair scope |
|---|---|---|---|
| `DOC.PAGE_COUNT_MISMATCH` | IR vs verified preflight pages | CRITICAL hard gate | Document/re-normalize |
| `DOC.MISSING_PAGE` | Gap/duplicate page numbers | CRITICAL hard gate | Page/document |
| `DOC.SUSPECT_CONTENT_LOSS` | Source text objects/image/text density vs IR by page | ERROR | Page |
| `DOC.DUPLICATE_PAGE_CONTENT` | Near-identical normalized content/geometry on non-template pages | ERROR | Page |
| `DOC.ABNORMAL_EMPTY_PAGE` | Nonblank raster/source signals but no flow blocks | ERROR | Page |
| `DOC.UNEXPECTED_PAGE_DIMENSION` | Large unexplained difference from preflight | CRITICAL | Page/normalization |

Intentional blank pages are not failures when raster entropy, source objects and neighboring pagination support blankness.

### 5.2 Text

| Rule code | Detection | Notes |
|---|---|---|
| `TEXT.ABNORMAL_CHARACTER_RATIO` | Control/private-use/replacement/unassigned ratios by language slice | Unicode-script aware |
| `TEXT.ENCODING_CORRUPTION` | mojibake signatures, replacement runs, impossible mappings | Never “fix” silently |
| `TEXT.OCR_GARBAGE` | token-length, entropy, script transitions, punctuation/repetition profile | Calibrated per zh/en/mixed |
| `TEXT.REPETITION_LOOP` | repeated n-grams/lines beyond legitimate headers | Region/page repair |
| `TEXT.LOW_DENSITY` | expected source/raster density vs extracted characters | Excludes figures/blank areas |
| `TEXT.DUPLICATED_BLOCK` | high text similarity + overlapping/near geometry | Merge/dedup candidate |
| `TEXT.LANGUAGE_MISMATCH` | strong profile hint vs extracted script | WARNING unless content loss evidence |

### 5.3 Layout/reading order

| Rule code | Detection | Notes |
|---|---|---|
| `LAYOUT.INVALID_BBOX` | NaN/out-of-page/non-positive geometry | CRITICAL hard gate |
| `LAYOUT.EXCESSIVE_OVERLAP` | IoU/intersection ratio for incompatible block types | Ignore intentional containment |
| `LAYOUT.ORDER_NOT_TOTAL` | duplicate/gapped order among flow blocks | ERROR |
| `LAYOUT.ORDER_CYCLE` | relationship DAG cycle | CRITICAL hard gate |
| `LAYOUT.COLUMN_JUMP` | geometry/order transitions inconsistent with inferred columns | Page repair |
| `LAYOUT.DISCONNECTED_CONTENT` | visible/source content without nearby canonical ownership | Region repair |
| `LAYOUT.CAPTION_ORPHAN` | caption without plausible figure/table | Region/page repair |
| `LAYOUT.REPEATED_MARGIN_MISCLASSIFIED` | repeated header/footer in body flow | Document heuristic, no fallback always needed |

### 5.4 Tables

| Rule code | Detection | Notes |
|---|---|---|
| `TABLE.INVALID_DIMENSIONS` | row/column count <= 0 or cell outside grid | CRITICAL |
| `TABLE.IMPOSSIBLE_SPAN` | zero/out-of-range/overlapping occupied grid | CRITICAL |
| `TABLE.INCONSISTENT_ROWS` | unexpected occupied-column pattern | Language/domain neutral |
| `TABLE.EXCESSIVE_EMPTY_CELLS` | empty ratio vs detected lines/text and table type | Slice calibrated |
| `TABLE.MISSING_COLUMNS` | alignment/grid evidence implies lost column | Table fallback |
| `TABLE.TEXT_OUTSIDE_CELLS` | table-region source text not assigned to cells | Table/region fallback |
| `TABLE.CROSS_PAGE_BREAK` | adjacent continuation evidence but no relationship | Neighbor-page repair/merge |
| `TABLE.REPEATED_HEADER_DUPLICATION` | repeated page header retained as data rows | Deterministic repair candidate |
| `TABLE.CELL_ORDER_ANOMALY` | cell reading order conflicts with grid | Table fallback |

### 5.5 Structure and provenance

| Rule code | Detection | Default |
|---|---|---|
| `STRUCTURE.INVALID_SECTION_TREE` | cycle, level jump without policy, partial overlap | ERROR/CRITICAL cycle |
| `STRUCTURE.HEADING_ORPHAN` | heading not represented in section forest | ERROR |
| `STRUCTURE.FOOTNOTE_ORPHAN` | footnote with no nearby target/evidence | WARNING |
| `PROVENANCE.MISSING` | published entity has no provenance | CRITICAL hard gate |
| `PROVENANCE.BROKEN_CHAIN` | artifact/parser-run/source path unresolved | CRITICAL hard gate |
| `PROVENANCE.BBOX_MISMATCH` | entity vs provenance geometry inconsistent | ERROR |
| `PROVENANCE.OUT_OF_SCOPE` | fallback content originates outside authorized target | CRITICAL hard gate |

## 6. Scoring

Scoring is a diagnostic and routing signal, never a replacement for hard gates.

For dimension `d`, each rule emits already scope-normalized `impact_i` in `[0, 0.95]`. Duplicate issues with the same deduplication key are collapsed to the maximum impact. The dimension score is:

```text
dimension_score[d] = product(1 - impact_i for open issues in d)
```

The overall score is a weighted geometric mean:

```text
quality_score = exp(sum(weight[d] * ln(max(dimension_score[d], 0.001))))
```

Default weights, configurable only by versioned ruleset:

```yaml
COMPLETENESS: 0.25
TEXT:         0.20
LAYOUT:       0.15
TABLE:        0.15
STRUCTURE:    0.10
PROVENANCE:   0.15
```

If a document has no applicable tables, the TABLE weight is redistributed proportionally. A hard-gate failure caps status at `FAIL` regardless of numeric score. The report stores raw issue impacts and weights for explainability.

### Thresholds and status

Default:

```yaml
pass_threshold: 0.80
fallback_trigger_score: 0.65
partial_publish_threshold: 0.50
```

Decision order:

1. Mandatory rule skipped or any open hard gate -> `FAIL`, `publishable=false`.
2. Score `< 0.65` -> `FAIL`.
3. Score `0.65..<0.80` or any open `ERROR` -> `DEGRADED`.
4. Score `>=0.80` with no open ERROR/CRITICAL -> `PASS`.
5. `PARTIAL` job publication is possible only when status is `DEGRADED`, score >= 0.50, `allow_partial=true`, no hard gate, complete page manifest, and every gap is disclosed. `QualityStatus` remains `DEGRADED`; job state is `PARTIAL`.

This prevents a high aggregate score from hiding a missing page or broken provenance.

## 7. Fallback decision

Fallback is not determined from score alone. The recommendation engine considers:

- open issue severity/impact and repairability;
- minimal target scope and capability availability;
- expected quality gain from historical benchmark/calibration;
- area/page/time/cost/fallback-round budgets;
- whether the same adapter/version already failed that scope;
- risk of cross-boundary damage.

Urgency:

- `REQUIRED`: repairable CRITICAL, repairable ERROR that blocks publication, or score below fallback trigger.
- `RECOMMENDED`: degraded targetable issue with positive expected gain and budget.
- `OPTIONAL`: warning-level improvement; not run by default in MVP.

`fallback_required` is true if at least one coalesced recommendation is `REQUIRED`. A PASS report normally has no executed fallback recommendation. A targeted WARNING can be configured as required for protected document classes, such as financial tables.

Recommendation coalescing merges overlapping issues only when their required capabilities and context boundaries agree. It must not convert three isolated regions into a page fallback solely for scheduling convenience unless the planner proves the page call stays within the configured area/cost threshold.

## 8. Example reports

### Pass

```json
{
  "quality_report_id": "qrep_01",
  "report_version": "1.0.0",
  "ruleset_version": "1.0.0",
  "document_id": "doc_01",
  "ir_revision_id": "rev_01",
  "validation_scope": {"kind": "DOCUMENT"},
  "dimension_scores": {"COMPLETENESS": 1.0, "TEXT": 0.94, "LAYOUT": 0.91, "TABLE": 0.90, "STRUCTURE": 0.93, "PROVENANCE": 1.0},
  "quality_score": 0.95,
  "status": "PASS",
  "publishable": true,
  "hard_gate_failures": [],
  "issues": [],
  "fallback_recommendations": [],
  "fallback_required": false,
  "evaluated_rules": ["DOC.PAGE_COUNT_MISMATCH@1.0.0"],
  "skipped_rules": [],
  "thresholds": {"pass": 0.8, "fallback_trigger": 0.65, "partial_publish": 0.5},
  "evidence_artifact_ids": []
}
```

### Failed table requiring fallback

```json
{
  "quality_report_id": "qrep_02",
  "report_version": "1.0.0",
  "ruleset_version": "1.0.0",
  "document_id": "doc_01",
  "ir_revision_id": "rev_02",
  "validation_scope": {"kind": "DOCUMENT"},
  "dimension_scores": {"COMPLETENESS": 1.0, "TEXT": 0.88, "LAYOUT": 0.82, "TABLE": 0.12, "STRUCTURE": 0.90, "PROVENANCE": 1.0},
  "quality_score": 0.64,
  "status": "FAIL",
  "publishable": false,
  "hard_gate_failures": [],
  "issues": [{
    "issue_id": "qis_02",
    "rule_id": "TABLE.IMPOSSIBLE_SPAN",
    "rule_version": "1.0.0",
    "type": "TABLE.IMPOSSIBLE_SPAN",
    "dimension": "TABLE",
    "severity": "ERROR",
    "message_safe": "Table cells overlap in the logical grid.",
    "scope": {"kind": "TABLE", "page_numbers": [23], "entity_id": "table_023_02", "bbox": [42.0, 180.0, 553.0, 410.0]},
    "evidence": {"overlapping_cell_pairs": 3},
    "impact": 0.70,
    "confidence": 1.0,
    "repairable": true,
    "candidate_capabilities": ["TABLE", "TABLE_SPANS", "REGION_INPUT"],
    "disposition": "OPEN",
    "provenance_ids": ["prov_02"]
  }],
  "fallback_recommendations": [{
    "recommendation_id": "frec_02",
    "issue_ids": ["qis_02"],
    "target_scope": {"kind": "TABLE", "page_numbers": [23], "entity_id": "table_023_02", "bbox": [42.0, 180.0, 553.0, 410.0]},
    "required_capabilities": ["TABLE", "TABLE_SPANS", "REGION_INPUT"],
    "urgency": "REQUIRED",
    "preferred_strategy": "REPLACE_TABLE_ATOMICALLY",
    "estimated_gain": 0.42,
    "estimated_cost": {"page_equivalents": 0.31},
    "boundary_context": {"padding_points": 12, "neighbor_pages": []},
    "constraints": {"preserve_external_caption_edges": true},
    "reason_codes": ["PUBLISH_BLOCKED", "LOCALIZED_TABLE_FAILURE"]
  }],
  "fallback_required": true,
  "evaluated_rules": ["TABLE.IMPOSSIBLE_SPAN@1.0.0"],
  "skipped_rules": [],
  "thresholds": {"pass": 0.8, "fallback_trigger": 0.65, "partial_publish": 0.5},
  "evidence_artifact_ids": ["art_table_debug_02"]
}
```

## 9. Calibration and change management

- Rule thresholds are learned/tuned only on training/development fixtures, never on the protected test split.
- Per-language/document-type calibration is allowed through explicit versioned profiles; missing slice configuration falls back conservatively.
- Every ruleset change emits a benchmark report showing issue-count and score distribution shifts, not only final pass rate.
- Rules that use statistical models must be small, local, pinned and deterministic under fixed inputs; they declare model digest and remain optional unless promoted by ADR.
- LLM-based semantic validation, if added, emits a separate advisory issue source and cannot be a hard gate without a later ADR and deterministic fallback path.

## 10. Testing and observability

- Unit fixtures for true positive, false positive guard and boundary values per rule.
- Property tests for bbox/order/table/graph invariants.
- Metamorphic tests: translation/scaling of coordinates, page reordering rejection, equivalent Unicode normalization.
- Golden issue snapshots and ruleset backward-compatibility tests.
- Fault tests for missing evidence, mandatory-rule timeout and partial rule execution.
- Metrics: issue count by rule/severity, dimension score histograms, validation duration, fallback recommendations, repair success rate and false-positive review rate. Labels exclude document/tenant IDs.
- Logs contain issue IDs and evidence artifact IDs, not extracted text.

# Calibrated Quality Gate Specification

| Field | Value |
|---|---|
| Status | Implemented MVP; calibration evidence not provisioned |
| Contract version | `quality-gate/1.2.0` |
| Decision policy | Evidence sufficiency and risk gating, not proof of correctness |
| Core policy | Deterministic/statistical first; no LLM required |

## 1. Purpose and boundary

Parser execution and output acceptance are separate facts. `ParseResult.status=SUCCESS` means the
adapter returned a complete neutral envelope. It does not mean the content is correct or safe to
index.

The Quality Gate answers one bounded question:

> Is the available evidence sufficient to accept this parsed scope automatically, or must the
> scope be reparsed/escalated or rejected?

The Quality Gate is not a correctness oracle. It cannot prove that an arbitrary document is
perfectly parsed. It consumes an immutable Canonical IR revision, `DocumentProfile`, native PDF
evidence, parser diagnostics and a versioned calibration profile. It emits facts and a policy
decision; it never silently edits IR.

## 2. Decision model and IR lifecycle

### 2.1 Primary decision

```text
QualityDecision:
  ACCEPT
  FALLBACK_REQUIRED
  REJECT
```

| Decision | Meaning | IR status | Publishable |
|---|---|---|:---:|
| `ACCEPT` | No hard failure; all mandatory applicable evidence passed the calibrated supported-slice policy | `PASS` | Yes |
| `FALLBACK_REQUIRED` | One or more reliably targetable failures are detected and an eligible alternate execution path may improve them | `DEGRADED` | No, until candidate comparison and revalidation accept a new revision |
| `REJECT` | Hard integrity failure, unsupported/insufficient evidence with no safe fallback, non-targetable failure, or exhausted repair | `FAIL` | No |

`PARTIAL` remains a job state, not a quality status. A later explicit policy may activate selected
`DEGRADED` scopes, but deny-by-default remains authoritative.

### 2.2 Continuous score policy

The MVP decision does not require a weighted continuous quality score. The prior weights
(`COMPLETENESS=0.25`, `TABLE=0.15`, and similar) and thresholds (`PASS=0.80`, fallback `0.65`) are
**UNCALIBRATED PROPOSALS** and must not control acceptance.

A continuous `quality_score` may be introduced only when protected-set evidence shows that it adds
decision value beyond the discrete rules. Its calibration target, reliability curve and decision
threshold must then be versioned and reported.

IR 1.2 implements the backward-compatible lifecycle amendment: evaluated
`PASS/DEGRADED/FAIL` may carry `score=null` for the discrete gate, while
`quality_report_id` remains mandatory. No synthetic `1.0`, `0.5`, or `0.0` is emitted.

Quality execution has exactly three modes:

- `OBSERVE_ONLY`: no applicable profile; signals are visible, but the result cannot be accepted,
  published, or sent to automatic fallback.
- `CALIBRATION`: an applicable candidate profile is present but is not frozen; its thresholds and
  actions compute real decisions for calibration metrics, while publication and automatic fallback
  remain disabled.
- `CALIBRATED`: an applicable frozen profile is bound to calibration evidence; its policy may
  accept content or authorize fallback planning.

`supported_slice` is an explicit Quality Gate input. It is not inferred from `FallbackProfile`.
An unsupported or omitted slice evaluates as `OBSERVE_ONLY` even when a profile is supplied.

## 3. Evidence and rule contracts

```python
class QualityRule(Protocol):
    def descriptor(self) -> RuleDescriptor: ...
    def evaluate(self, context: ValidationContext) -> tuple[QualitySignal, ...]: ...

class QualityGate(Protocol):
    def decide(self, request: ValidationRequest) -> QualityReport: ...
```

Every `RuleDescriptor` must define:

| Field | Contract |
|---|---|
| `rule_id`, `rule_version` | Stable machine identity and behavior version |
| `evidence_required` | Exact IR/preflight/native/raster/parser evidence fields |
| `scope` | `DOCUMENT`, `PAGE`, `REGION`, `TABLE`, `FIGURE`, or `BLOCK` |
| `applicability` | Deterministic predicate; false means `NOT_APPLICABLE`, not pass |
| `signal_class` | `HARD_INTEGRITY`, `CROSS_SOURCE_DISAGREEMENT`, `ANOMALY`, or `PARSER_UNCERTAINTY` |
| `deterministic` | Whether identical evidence must produce identical output |
| `predicted_failure_type` | Defect label the signal attempts to detect |
| `output_severity` | Default `INFO/WARNING/ERROR/CRITICAL` |
| `false_positive_mode` | Known correct inputs likely to trigger the rule |
| `false_negative_mode` | Known defects likely to escape the rule |
| `repairability` | Target capability/scope, or `NONE` |
| `calibration_profile_id` | Frozen calibration evidence or `PROVISIONAL` |
| `complexity_budget` | Bounded operations/evidence and timeout behavior |

### 3.1 Signal classes

1. **Hard integrity failures** are deterministic violations such as missing pages, invalid geometry,
   broken provenance or impossible table occupancy. They block publication independently of any
   score.
2. **Cross-source disagreement signals** compare independent evidence such as native PDF numbers
   and parser output. They establish disagreement, not which source is correct.
3. **Statistical/anomaly signals** detect unusual content loss/density/sparsity relative to a
   calibrated slice. They require documented false-positive/false-negative behavior.
4. **Parser uncertainty signals** preserve unresolved order, unknown hierarchy, parser warnings or
   missing confidence. Parser confidence alone never authorizes acceptance or replacement.

### 3.2 `QualitySignal`

Required fields:

```text
signal_id, rule_id, rule_version, signal_class, failure_type,
scope, applicability, severity, evidence_refs, observed_values,
threshold_profile_id, deterministic, repairable, candidate_capabilities,
false_positive_mode, false_negative_mode, provenance_ids
```

Evidence is bounded and redacted by default. A signal is a fact/prediction, not a final decision.
Signals from the same evidence may be deduplicated, but one weak signal cannot be multiplied into a
strong decision by emitting duplicates.

### 3.3 `QualityReport`

```text
QualityReport
├── quality_report_id, report_version, ruleset_version, policy_version
├── calibration_profile_id, benchmark_dataset_digest
├── document_id, ir_revision_id, validation_scope
├── decision: ACCEPT | FALLBACK_REQUIRED | REJECT
├── quality_status: PASS | DEGRADED | FAIL
├── publishable
├── quality_score: number | null
├── score_model: NONE | <versioned model id>
├── signals[]
├── hard_failures[]
├── fallback_recommendations[]
├── evaluated_rules[], not_applicable_rules[], skipped_rules[]
├── evidence_coverage
└── started_at, ended_at, report_digest
```

An applicable mandatory rule that is skipped or times out prevents `ACCEPT`. A rule that lacks its
required evidence emits `INSUFFICIENT_EVIDENCE`; policy chooses fallback or reject. Missing evidence
is never equivalent to passing evidence.

## 4. MVP rule set

The first implementation is deliberately small. Additional rules require observed failure examples
and calibration evidence.

### 4.1 Hard integrity

| Rule | Evidence | Decision behavior |
|---|---|---|
| `INTEGRITY.PAGE_COUNT_MISMATCH` | verified preflight page count and IR pages | `REJECT`, or `FALLBACK_REQUIRED` only when missing page scope is executable and identifiable |
| `INTEGRITY.MISSING_OR_DUPLICATE_PAGE` | ordered page registry | Same as above |
| `INTEGRITY.INVALID_BBOX` | canonical page geometry and spatial entities | `REJECT`; normalization defect is not accepted |
| `INTEGRITY.BROKEN_PROVENANCE` | source/provenance/parser-run graph | `REJECT` |
| `INTEGRITY.TABLE_GRID_INVALID` | table dimensions, spans and occupied grid | `REJECT` current revision; targetable table candidate may be requested |

Most of these are already Canonical IR invariants. The Quality Gate must reuse the invariant result
or map a normalization failure into a report; it must not implement a second divergent validator.

### 4.2 Completeness

`COMPLETENESS.SOURCE_RICH_PARSE_SPARSE` applies only when cheap source/raster evidence indicates a
nonblank page and parser output is abnormally sparse. Evidence includes native character count,
image-only classification, parser block characters, table-cell characters and optionally bounded
raster occupancy. Thresholds are slice-specific and provisional until calibrated. Intentional blank
pages, full-page figures and broken-font native extraction are explicit false-positive modes.

### 4.3 Numeric evidence

`NUMERIC.NATIVE_PARSER_DISAGREEMENT` compares normalized numeric **multisets**, not sets. It reports
exact overlap, missing native multiplicity and extra parser multiplicity. It is cross-source
disagreement: native text is not assumed correct.

Page token presence and structural correctness are separate:

- runtime page-level disagreement may trigger review/fallback after calibration;
- benchmark table-cell truth measures whether the number is in the correct logical table/cell;
- a number elsewhere on the page cannot satisfy structural numeric correctness.

### 4.4 Table evidence

| Rule | Applicability and behavior |
|---|---|
| `TABLE.REGION_TEXT_ASSIGNMENT_SPARSE` | Only applies when trustworthy source/table-region text evidence exists. Compare source-supported tokens/lines with cell assignment coverage. No positional evidence means `NOT_APPLICABLE`, not pass. |
| `TABLE.LOGICAL_OCCUPANCY_INVALID` | Deterministic reuse of table-grid invariant: dimensions/spans/overlap/out-of-grid are hard failures. |

No rule infers missing columns solely from visual intuition or parser confidence in the MVP.

### 4.5 Reading order

| Rule | Behavior |
|---|---|
| `ORDER.UNRESOLVED` | Emits affected page/block scope. It may require page fallback for multicolumn supported slices after calibration; it is not silently sorted by geometry. |
| `ORDER.DUPLICATE_OR_CYCLE` | Duplicate canonical order or explicit order cycle is a hard integrity failure. |

## 5. Decision policy

Evaluation order is deterministic:

1. Validate required evidence and Canonical IR hard invariants.
2. Run applicable hard-integrity rules.
3. Run calibrated disagreement/anomaly/uncertainty rules within their evidence budgets.
4. Map signals to declared failure scopes.
5. Apply the frozen policy table; no issue-count arithmetic or parser confidence voting.

Default policy:

```text
if any unrecoverable hard failure:
    REJECT
elif any calibrated blocking signal with reliable executable target:
    FALLBACK_REQUIRED
elif any mandatory evidence missing or applicable mandatory rule skipped:
    REJECT
elif document/scope is outside calibrated supported slices:
    REJECT (or explicit manual-review policy outside this MVP)
else:
    ACCEPT
```

An `ACCEPT` decision means the output met the declared supported-slice correctness policy under the
measured detector coverage. It does not mean every semantic fact in the document is correct.

## 6. Fallback recommendation

A recommendation contains `recommendation_id`, contributing signal IDs, minimal target scope,
required capabilities, boundary context, evidence coverage, calibrated detection precision/recall,
expected repair evidence, budget and reason codes. It does not name a parser unless capability,
license, runtime and fixed-corpus benchmark evidence have already selected an eligible candidate.

The recommendation engine must not:

- use raw parser confidence as the replacement decision;
- broaden a target for scheduling convenience;
- issue a selective scope unsupported by the actual adapter;
- estimate quality gain without a benchmark profile;
- execute fallback before the policy and threshold profile are frozen.

## 7. Calibration contract

### 7.1 Datasets

- `development`: rule implementation and error analysis.
- `calibration`: threshold/policy selection; document families separated from development.
- `protected_holdout`: one evaluation after policy freeze; no threshold tuning.

Every sample has defect labels, affected scope, supported-slice label and an adjudicated
`meets_acceptance_standard` outcome. Document-family/template leakage is prohibited.

`CalibrationTruth.failure_labels[]` stores zero or more `FailureLabel` values. Each label contains
`rule_id`, `scope=DOCUMENT|PAGE|TABLE`, and the required `page_number`/`table_id` identity. Rule
confusion matrices compare canonical `(rule_id, scope, page_number, table_id)` keys. A prediction
on the wrong page/table is one false positive plus one false negative, never a true positive.

### 7.2 Rule-level metrics

For every applicable rule, report raw `TP`, `FP`, `TN`, `FN` and:

```text
detection_precision = TP / (TP + FP)
detection_recall    = TP / (TP + FN)
false_positive_rate = FP / (FP + TN)
false_negative_rate = FN / (FN + TP)
```

Undefined denominators are reported as `N/A`, never zero or one. Report by rule, failure type,
scope, language/document slice and dataset split with document-level confidence intervals.

### 7.3 System-level metrics

```text
accepted_output_precision = accepted outputs meeting declared standard / all accepted outputs
coverage                  = accepted eligible outputs / all eligible outputs
fallback_rate             = outputs sent to fallback / all eligible outputs
unresolved_failure_rate   = defective outputs neither repaired nor safely accepted / all eligible outputs
```

Accepted-output precision is always reported beside coverage and the supported-slice definition.
`accepted_output_precision >= 95%` means at least 95% of automatically accepted outputs meet that
declared correctness standard. It does **not** mean 95% of arbitrary PDFs are perfectly parsed.
No quality number may be published without its denominator, coverage, slice and confidence interval.

Every acceptance result declares exactly one `acceptance_unit`: `DOCUMENT`, `PAGE`, or `TABLE`.
Numerators and denominators are computed within that unit and must never be pooled across units.
Thus document accepted precision, page accepted precision and table accepted precision are three
different results. Any future 95% claim must name the acceptance unit, supported slice, sample size,
coverage and uncertainty/confidence-interval method.

### 7.4 Threshold freeze and promotion

1. Record rule/policy versions, dataset digests and all candidate thresholds.
2. Tune only on development/calibration data.
3. Freeze the selected policy before opening the protected holdout.
4. Evaluate accepted precision, coverage, fallback rate and unresolved failure rate on holdout.
5. Promote only if critical-slice requirements and sample adequacy are met; otherwise expand data
   or narrow supported slices. Never repeatedly tune against the holdout.

`freeze_profile()` must match `profile_id` and `dataset_digest`, require a non-empty calibration
report, and persist `calibration_report_digest` plus `calibration_sample_count`. `frozen=true`
therefore proves artifact lineage only; it does not assert that any target precision was achieved.

## 8. Native PDF evidence semantics

Native PDF text is evidence, not ground truth. Preflight must eventually classify:

```text
BORN_DIGITAL_NATIVE
OCR_BACKED_HIDDEN_LAYER
BROKEN_OR_UNRELIABLE_EXTRACTION
IMAGE_ONLY
UNKNOWN
```

`has_text_layer=true` means extraction returned characters. It does not mean those characters are
correct, complete or native authoring text. OCR-backed layers may duplicate visible content;
custom/broken fonts may extract garbage; image-only pages have no textual comparator. Classification
is heuristic and carries evidence/reason codes. Numeric disagreement rules disclose this class and
become non-applicable when source evidence is unreliable.

## 9. Testing and observability

- Known-answer tests per rule: true positive, false-positive guard, false-negative example and
  applicability boundary.
- Golden confusion-matrix vectors and policy decision tables.
- Property tests for geometry/table/order invariants reuse Canonical validators.
- Tests proving missing evidence and timeout cannot become `ACCEPT`.
- Calibration runner tests for denominators, macro/micro aggregation, slice isolation and holdout
  freeze enforcement.
- Structured telemetry: signal count by rule/severity/decision, rule latency/budget exhaustion,
  fallback rate, accepted precision/coverage report ID and unresolved failures. No document text or
  high-cardinality IDs in metric labels.

## 10. Explicit non-goals

- LLM judge or LLM repair in the MVP gate.
- A large speculative rule catalog.
- Treating parser confidence as calibrated correctness.
- Claiming a discrete gate proves semantic correctness.
- Automatically publishing `DEGRADED` output.

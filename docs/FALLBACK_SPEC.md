# Selective Fallback and Merge Specification

| Field | Value |
|---|---|
| Status | Authoritative design; implementation blocked on calibrated Quality Gate |
| Merge algorithm version | `1.0.0` |
| Core property | Scope-minimal, quality-improving, transactional, provenance-preserving |

## 1. Objectives and non-objectives

Fallback repairs a validated defect without discarding correct primary output. It supports `DOCUMENT`, `PAGE`, `REGION`, `TABLE`, `FIGURE` and `BLOCK` targets. It must explain why a target was selected, what was replaced, how identities were resolved and whether quality improved.

Fallback is not:

- a second parser run over every page “for safety”;
- a union of two parser outputs;
- selection by parser confidence alone;
- destructive mutation of the baseline revision;
- unlimited retry/repair iteration.

### 1.1 Activation prerequisites

Automatic fallback is prohibited until all of the following are true:

1. the corrected benchmark protocol has frozen baseline results for the applicable document slice;
2. Quality Gate signals have measured detection precision/recall and a frozen calibration profile;
3. the defective scope can be identified with adequate target precision;
4. the alternate adapter truthfully supports that executable scope;
5. candidate-vs-baseline comparison and full revalidation are available.

Current Docling and Paddle adapters execute `DOCUMENT` scope. They are parser candidates, not yet
selective page/table fallback executors. The planner must never advertise a scope the adapter cannot
execute. No profile rule such as “Paddle always wins tables” or “Docling wins born-digital” is valid
without fixed-corpus evidence.

The authoritative control flow is:

```text
baseline parser -> Canonical IR -> calibrated Quality Gate
  -> minimal reliable target -> alternate candidate
  -> candidate-vs-baseline comparison -> full revalidation
  -> transactional commit only when demonstrably improved
```

## 2. Inputs and outputs

### `FallbackPlan`

```text
FallbackPlan
├── plan_id, plan_version
├── document_id, baseline_revision_id, quality_report_id
├── targets[]
│   ├── target_id, scope, issue_ids[]
│   ├── required_capabilities[]
│   ├── selected_adapter_id + descriptor_digest
│   ├── input_materialization
│   ├── merge_strategy
│   ├── boundary_context
│   ├── expected_gain, estimated_cost
│   └── budgets/deadline
├── total_budget
└── reason_codes[]
```

### `MergeResult`

Required: `merge_operation_id`, `status` (`APPLIED`, `REJECTED`, `CONFLICT`, `STALE`), baseline/candidate/new revision IDs, target scope, matches, additions, replacements, removals, preserved boundary edges, duplicate suppressions, confidence decisions, provenance additions, validation before/after, quality delta, diagnostics artifact and deterministic result digest.

Rejected candidates remain immutable diagnostic artifacts; they do not alter the published revision.

## 3. Detection and target planning

The planner consumes `QualityReport.fallback_recommendations`, adapter descriptors, budgets, worker availability and benchmark eligibility.

### 3.1 Minimal-scope rule

Select the smallest scope that contains all evidence and enough context for a valid repair:

| Issue | Default target | Context |
|---|---|---|
| OCR garbage paragraph | BLOCK or REGION | neighboring lines, 8–16 pt padding |
| Impossible table span | TABLE | full table bbox + caption/header context |
| Reading order anomaly in column | REGION or PAGE | predecessor/successor anchors |
| Orphan figure caption | FIGURE group/REGION | figure + candidate captions |
| Nonblank empty page | PAGE | full page |
| Cross-page table break | TABLE with both segments | previous/current/next segment pages, not entire document |
| Missing multiple unrelated pages | PAGE set | individually checkpointed |
| Widespread normalization/coordinate failure | DOCUMENT | only if systemic and justified |

Target expansion is allowed only for declared context. Expansion decisions record `from_scope`, `to_scope` and reason. Scheduling convenience is not a reason.

### 3.2 Coalescing

Two targets may coalesce when:

- scopes overlap or are within configured padding;
- required capabilities and selected adapter are compatible;
- combined area/page cost is less than separate cost;
- merge atomicity boundaries agree (e.g., same table);
- coalescing does not exceed max area ratio or introduce unrelated body content.

Table/figure atomic groups cannot be split merely to reduce pixels when doing so would detach cells/captions.

### 3.3 Budgets and loop prevention

Illustrative plan limits (all **PROVISIONAL** until workload/calibration evidence exists):

```yaml
max_fallback_rounds: 1
max_fallback_pages: min(50, ceil(page_count * 0.20))
max_fallback_area_ratio: 0.30
max_target_attempts: 2
    minimum_expected_gain: null
    minimum_applied_score_delta: null
```

An attempt fingerprint covers target, adapter/model, render/config and baseline revision. The same fingerprint cannot run twice. A second adapter attempt is permitted only after a typed first-attempt failure or candidate rejection and within budget.

## 4. Parser selection

Selection filters, then ranks:

1. Adapter/model/license/security approval is active.
2. Required input and semantic capabilities are declared and contract-tested.
3. Scope, language, pixels/pages and device fit resource constraints.
4. The adapter/model did not produce the failing primary evidence for that scope, unless only a changed deterministic mode is explicitly justified.
5. Candidate passed minimum benchmark thresholds for the issue/document slice.

Rank only among eligible candidates using frozen, issue-specific benchmark evidence, reliability,
cost/latency and complementarity. Parser name is configuration data; selection logic operates on
descriptors and benchmark profiles. Raw parser confidence is evidence at most; it cannot select a
candidate or authorize replacement.

If no parser is capable, emit `FALLBACK.NO_CAPABLE_PARSER`; do not broaden to document scope automatically.

## 5. Input materialization and coordinate normalization

For native region-capable adapters, pass the authorized page/crop. Otherwise:

1. Intersect target bbox with canonical page bounds.
2. Add bounded context padding and record actual clipped padding.
3. Render at versioned DPI with effective page rotation applied.
4. Store crop artifact keyed by source/page/canonical bbox/padding/DPI/renderer version.
5. Provide the affine `crop pixels -> canonical points` transform in `ParseRequest`.
6. Reject any returned geometry outside crop plus tolerance.

Candidate normalization runs the same strict Canonical rules as primary normalization, into an isolated `CandidateFragment`, not directly into the baseline document. Transform checks include corners, center, round-trip tolerance and bbox containment. Text-only candidate elements without geometry cannot replace spatial entities unless the target strategy explicitly permits it and retains baseline geometry with `DERIVED` provenance.

## 6. Merge preconditions

Before matching:

- baseline revision/digest equals the plan precondition;
- target entity IDs and boundary anchors still exist;
- candidate covers the requested target or reports explicit partial scopes;
- candidate fragment passes schema, coordinate, provenance and target-specific hard invariants;
- source/document/page identities match;
- no candidate content originates outside the authorized scope;
- fallback adapter/model/config differs from a known deterministic failed fingerprint.

A stale baseline returns `STALE`; the orchestrator may re-plan once against the latest revision. It never applies the old patch optimistically.

## 7. Entity matching algorithm

### 7.1 Target set and anchors

- `T`: baseline entities whose geometry/semantic group intersects the target and are eligible for replacement.
- `A_before`, `A_after`: nearest unaffected in-flow entities before/after the target in reading order.
- `C`: normalized candidate entities.
- Boundary relationships from outside `T` into `T` are captured as typed anchors for later rewiring.

Decorative repeated headers/footers are excluded unless explicitly targeted. For a table/figure, `T` includes its block, child entities, captions and internal relationships according to merge strategy.

### 7.2 Pair compatibility and score

Pairs with impossible page/type/geometry are removed. For remaining baseline `t` and candidate `c`:

```text
match_score(t,c) =
    0.35 * geometry_similarity
  + 0.25 * type_compatibility
  + 0.20 * text_similarity
  + 0.10 * neighborhood_similarity
  + 0.10 * structure_similarity
```

- Geometry combines bbox IoU and normalized center/size distance.
- Type compatibility uses a versioned matrix; e.g. `PARAGRAPH <-> QUOTE` may be weakly compatible, `TABLE <-> PAGE_NUMBER` is impossible.
- Text similarity uses Unicode/script-aware normalized edit/token similarity only for matching; canonical text is not normalized destructively.
- Neighborhood compares relative order and nearby type signatures.
- Structure compares table grid/section/caption roles.

Weights/thresholds are merge-version configuration and benchmarked. Missing text/confidence does not become a perfect match.

### 7.3 One-to-one assignment

Build a sparse bipartite graph for scores >= `candidate_threshold` and compute deterministic maximum-weight matching (Hungarian/min-cost flow with stable ID tie-breaks). Accept one-to-one matches above `accept_threshold` and margin over the second-best candidate. Ambiguous matches become conflicts, not arbitrary replacements.

### 7.4 Split/merge classification

Unmatched local clusters are tested for:

- **one-to-many split:** union candidate geometry covers one baseline entity, concatenated candidate text resembles baseline or candidate quality fixes a known segmentation issue;
- **many-to-one merge:** candidate covers adjacent baseline entities with compatible type/order and resolves duplication/hyphenation issue;
- **unmatched addition:** visible candidate content is supported by source evidence and not a duplicate;
- **unmatched removal:** baseline content is proven duplicate/spurious or explicitly superseded by an atomic structure replacement.

Split/merge requires stronger thresholds and explicit reason codes. It always creates new entity IDs and `SUPERSEDES`/`DERIVED_FROM` relationships. One-to-one semantic replacement may preserve the baseline canonical ID while adding both provenance chains.

## 8. Candidate-vs-baseline decision

Matching says which entities correspond; it does not decide that fallback is better.

For each atomic replacement group:

1. Validate baseline group and candidate group with the same applicable local rules.
2. Compare hard invariants, the frozen issue-specific acceptance predicate, content coverage and boundary consistency.
3. Apply only if the candidate has no new hard failure, resolves the triggering issue, and changes
   the target from non-acceptable to acceptable (or produces a predeclared material metric
   improvement when both remain non-acceptable for a manual-review workflow).
4. When evidence cannot demonstrate material improvement, preserve primary and record
   `REJECTED_NO_CLEAR_GAIN`.
5. A parser confidence increase alone cannot authorize replacement.

Confidence is calibrated by adapter/model/slice before comparison. Merged confidence is a versioned derived value from accepted candidate confidence, source evidence, validator improvement and agreement. It is never a raw maximum or average across incomparable parsers.

## 9. Replacement strategies

### 9.1 Text/layout block

- Preserve ID for confident one-to-one semantic replacement.
- Candidate supplies text/type/geometry only for fields justified by its capability.
- Preserve unaffected relationships/styles if still valid; never copy parser-specific metadata across adapters.
- Add candidate provenance, retain baseline provenance and record operation.

### 9.2 Table — atomic semantic replacement

Table replacement operates on the logical `Table`, its segments/cells, table block(s), captions and internal edges. Partial cell replacement is prohibited in MVP because two table coordinate/grid systems can create an apparently valid but semantically inconsistent table.

- Reuse table/block ID only for one logical-table match; cells receive IDs according to one-to-one/split/merge matching.
- Validate occupied grid, spans, text coverage, header inference and segment ranges.
- Reconnect external caption/footnote/reference edges after compatibility checks.
- For cross-page tables, candidate may replace only affected segments, but the logical table grid is revalidated across all segments. Neighbor pages may be read/revalidated without being reparsed.

### 9.3 Figure/caption group

Image asset, figure block, OCR-inside-figure blocks and captions are separate. A figure fallback cannot delete OCR text because its layout label says “diagram”; source visual/text coverage rules must pass. Caption reassignment must beat the existing spatial/semantic edge by a configured margin.

### 9.4 Reading order

Do not renumber the entire document blindly. Build a local directed order graph from:

- candidate internal order;
- geometric column constraints;
- preserved `A_before -> target` and `target -> A_after` anchors;
- table/figure atomic ordering constraints.

Topologically sort with stable geometric tie-breaks. A cycle rejects the patch. Then assign contiguous per-page integers; downstream IDs do not depend on reading-order integer alone.

## 10. Duplicate suppression

After replacement and before commit, evaluate duplicates only in the changed scope plus overlap halo:

- exact normalized text + high IoU;
- high text similarity + containment/near-identical baseline source evidence;
- table cell text already owned by the accepted table;
- repeated margin templates classified as header/footer.

Suppression requires a designated survivor and provenance/relationship rewiring. Similar text in separate columns, bilingual duplicates, repeated table headers and legitimate citations are protected by geometry/type/section rules. No global fuzzy-text deduplication is allowed.

## 11. Conflict resolution

Conflict policy is conservative:

| Conflict | Resolution |
|---|---|
| Same entity, fallback clearly better | Replace per atomic strategy |
| Scores within tie/uncertainty margin | Keep primary, record alternative diagnostic |
| Fallback adds source-supported missing content | Add with new ID and provenance |
| Parsers disagree on type but text/geometry match | Use type-specific validator evidence; otherwise keep primary |
| Candidate bbox better, text worse | Field-level replacement only if capability/validation proves independence; otherwise reject group |
| Candidate out of target | Reject candidate hard |
| Two fallback targets overlap | Serialize by deterministic plan order or merge plans; stale second target re-plans |
| External relationship target removed | Rewire only through accepted semantic match; otherwise reject or surface issue |

MVP does not expose unresolved alternatives as published duplicate content. Diagnostics may retain them outside canonical published entities.

## 12. Transaction and commit algorithm

```text
1. Load baseline revision by digest and acquire document-revision merge lease.
2. Verify fencing token and FallbackPlan preconditions.
3. Normalize candidate into isolated fragment; run fragment hard gates.
4. Capture target entities, boundary anchors and dependent structures.
5. Compute pair scores, assignment, split/merge/add/remove classifications.
6. Build copy-on-write patch and provenance/relationship rewrites.
7. Run duplicate suppression in changed scope + halo.
8. Rebuild local order and affected section/table structures.
9. Validate changed scopes, dependencies and all document hard invariants.
10. Compare quality and apply/reject policy.
11. Serialize immutable candidate revision, verify digest and persist diagnostics.
12. In one metadata transaction, confirm fence/base revision, insert revision/report,
    advance manifest pointer and complete target checkpoint.
```

Any error through step 10 leaves the baseline untouched. A crash after object write but before metadata commit leaves an unreachable object for later garbage collection. A crash after metadata commit is complete because manifest/artifact digests were already verified.

## 13. Provenance rules

- Accepted one-to-one entity contains provenance from both primary and fallback plus `operation=REPLACE_ONE_TO_ONE`.
- Split/merge entities point to every contributing provenance record and have `SUPERSEDES`/`DERIVED_FROM` relationships.
- Removed entities remain in the previous immutable revision and in the merge diagnostics; they are not erased from history.
- The merge record stores adapter/model/config, target crop transform, match scores, quality before/after and decision reason.
- Export/chunk provenance always follows the new accepted revision; stale chunks cannot be reused when source entity identity/content changed.

## 14. Edge cases and required behavior

1. **Rotated/cropped page:** transform corners and center; reject mismatch rather than shift heuristically.
2. **Primary one paragraph, fallback three:** accept split only with union geometry/order/source coverage; new IDs.
3. **Primary duplicate overlays, fallback one:** many-to-one merge may remove duplicates with provenance.
4. **Bilingual parallel columns with similar text:** geometry/column context prevents false deduplication.
5. **Table crosses page boundary:** replace segment(s), reconcile repeated headers, validate logical grid on both pages.
6. **Merged cell spans page break:** do not create a physical cell spanning pages; use logical cell/segment provenance or reject unsupported representation.
7. **Fallback table bbox includes caption:** separate caption by type/edge; table atomicity does not absorb caption text.
8. **Figure contains important text:** figure label cannot suppress text coverage; preserve/add OCR-inside-figure blocks.
9. **Footnote target inside replaced table:** rewire external edge to matched table/cell or reject.
10. **Primary confidence missing:** compare validator evidence, not null coercion.
11. **Fallback output is empty:** legitimate only if target source evidence is blank; otherwise reject.
12. **Fallback returns full page for region call:** clip is not enough; reject out-of-scope envelope unless adapter wrapper enforced input crop.
13. **Reading-order tie:** use stable geometry/ID tie-break; record low confidence/issue where semantic order remains ambiguous.
14. **Concurrent merge:** fencing/base digest makes one stale; re-plan at most once.
15. **Parser hallucinated content:** source text/raster support and content-coverage rules reject unsupported addition.
16. **Page dimensions differ by parser:** canonical transform normalizes; unexplained aspect mismatch is hard failure.
17. **Header repeated inside fallback crop:** repeated-margin rule prevents inserting it into body flow.
18. **Candidate fixes trigger but breaks provenance:** reject; provenance hard gates cannot trade off against text quality.
19. **No compatible fallback parser:** preserve baseline, transition PARTIAL/FAILED by policy with explicit issue.
20. **Fallback budget exhausted:** stop deterministically; no whole-document escape unless it was pre-authorized by policy.

## 15. Whole-document fallback

Whole-document fallback is a distinct candidate revision, not an in-place mass replacement. It requires planner reason `WHOLE_DOCUMENT_JUSTIFIED` and one of:

- systemic source/normalizer incompatibility;
- failed-page ratio above configured threshold;
- affected-area ratio makes selective parsing demonstrably more expensive;
- adapter cannot address required isolated scopes but benchmark shows document-level benefit.

It still runs quality validation and candidate-vs-baseline comparison. Unaffected primary output is not automatically discarded; the entire candidate must pass publication and provenance gates. The event and cost are separately observable.

## 16. Tests and observability

- Deterministic golden match matrices for one-to-one/split/merge/add/remove/conflict.
- Property tests for assignment uniqueness, grid integrity, acyclic order and referential integrity.
- Coordinate metamorphic tests across DPI, crop padding and rotations.
- Transaction/fencing/crash tests at every commit boundary.
- Adversarial duplicates: bilingual columns, repeated headers, same-value table cells.
- Cross-page table, figure-caption, footnote and section-boundary golden fixtures.
- Candidate-quality no-regression tests and rejected-candidate immutability.
- Metrics: fallback target/page/area rate, attempts, applied/rejected/conflict, acceptance transition,
  issue-specific metric delta, repair success by issue/adapter, merge latency and whole-document
  fallback rate. A continuous score delta is recorded only when a calibrated score model exists.

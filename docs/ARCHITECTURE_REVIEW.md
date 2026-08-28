# Adversarial Architecture Review

| Field | Value |
|---|---|
| Review date | 2026-08-28 |
| Role | Contrarian Principal Architect |
| Scope | Phase 1 specifications before implementation |
| Outcome | Conditionally acceptable after listed corrections; parser recommendation remains provisional |

## 1. Review method

The review assumes malformed inputs, incorrect parser confidence, 1000-page outliers, one constrained GPU, process crashes at commit boundaries, parser/model upgrades, adversarial layouts and downstream consumers that misuse ambiguous fields. A risk is not “closed” by documentation alone; the correction must create an implementable invariant, test or promotion gate.

## 2. Scalability attacks

| ID | Attack / failure mode | Impact | Correction |
|---|---|---|---|
| S-01 | A 1000-page IR with millions of blocks/cells is constructed and serialized as one in-memory JSON object. | RAM spikes, long GC pauses, failed resume/export. | Define logical IR manifest + page/entity JSONL shards, bounded in-memory assembly and shard-independent IDs; update IR/storage/architecture. |
| S-02 | Overlap/duplicate/matching rules compare every block pair on a dense page/document. | O(n²) CPU and denial of service. | Require spatial/text indexes, local halos, per-rule operation/evidence budgets and explicit incomplete mandatory-rule failure; update quality spec. |
| S-03 | Docling primary models and PaddleOCR-VL fallback are assumed to coexist on one GPU. | Startup OOM, model thrashing and unpredictable latency. | Declare residency groups; primary may run CPU, fallback uses GPU, or scheduler serializes unload/load with measured cold penalty. Co-residency is a benchmark result, never an assumption. |
| S-04 | SQLite polling/writes become the bottleneck before anyone has defined a migration signal. | Lock contention, queue delay and stalled checkpoints. | Add measured triggers for PostgreSQL/scheduler migration: multi-host requirement, sustained busy/commit latency or queue dispatch share; update architecture. |
| S-05 | Page/crop render cache and raw parser output grow faster than document storage. | Disk exhaustion stops commits and may corrupt operational availability. | Retention classes, pixel/page estimates, reservation, high/critical watermarks, cache keys/eviction and artifact GC already exist; add sharded canonical layout and capacity gate. |

## 3. Reliability attacks

| ID | Attack / failure mode | Impact | Correction |
|---|---|---|---|
| R-01 | A parser returns 7 results for an 8-page batch and adapter marks the batch complete. | Silent missing page despite durable checkpoint. | Contract requires requested/completed/failed scope cardinality and per-page digest; checkpoint only explicitly complete scopes. Add adapter test emphasis. |
| R-02 | A long GPU call outlives its job lease; another worker retries and both attempt commit. | Duplicate work or stale revision activation. | Supervisor/watchdog renews lease independently of parser call; fenced commit remains authoritative. Add maximum non-renewable call and safe kill behavior. |
| R-03 | Cancellation reaches Python but native renderer/GPU call hangs. | Job remains `CANCELLING`, leaks VRAM/scratch. | Graceful poll then process-group kill and worker/CUDA-context recycle; supervisor owns cleanup. Already specified; add bounded cancellation SLO/test. |
| R-04 | Local artifacts are backed up at a different point than SQLite active manifests. | Restored database references missing/newer objects. | Require snapshot epoch: SQLite backup/checkpoint plus immutable artifact reachability manifest/digests and restore verification. Clarify storage. |
| R-05 | Fallback fixes one rule, revalidation triggers a new target, and loops indefinitely. | Cost explosion and unstable output. | Attempt fingerprints, one default round, page/area/time/cost budget and non-positive-gain stop already specified; make second-round activation a later explicit policy/version. |

## 4. Data-model attacks

| ID | Attack / failure mode | Impact | Correction |
|---|---|---|---|
| D-01 | A bilingual/mixed-style block has one bbox/style/provenance, so a quoted answer cannot map a character range precisely. | Highlight and provenance precision degrade. | Add optional `TextSpan` ranges with style/language/bbox/provenance; spans must cover without overlap when present. |
| D-02 | A logical table cell is visually fragmented across page segments, but `TableCell` has one page/bbox. | Cross-page citations are false or incomplete. | Add `TableCell.fragments[]` carrying page/segment/bbox/provenance; anchor page/bbox remains convenience only. |
| D-03 | Parser extension fields shadow canonical meaning (e.g. an extension says different page/order/type). | Two competing truths and downstream inconsistency. | Explicitly prohibit extensions from changing canonical semantics; semantic extension promotion requires schema change. |
| D-04 | Parser-origin-derived block IDs are mistaken for stable identities across parser/pipeline upgrades. | Stale citations/diffs and incorrect upserts. | Keep explicit limited stability, add optional `semantic_fingerprint` only for candidate matching/diff/cache hints, never as identity or authorization key. |
| D-05 | One giant JSON is called canonical although sharded representations are later added ad hoc. | Wire ambiguity and incompatible consumers. | Define a canonical logical model plus versioned IR manifest/shards; monolithic JSON remains normative small-document representation, shards are a lossless packaging profile. |

## 5. Parser integration attacks

| ID | Attack / failure mode | Impact | Correction |
|---|---|---|---|
| P-01 | Static capability says `TABLE` but only for simple English tables or a different model/backend. | Router selects an incapable fallback. | Descriptor binds capabilities/limitations to exact parser/model/backend/options; runtime readiness and benchmark eligibility filter each call. |
| P-02 | Upstream private output changes in a patch release while adapter version remains unchanged. | Silent mapping corruption. | Pin exact version/digest; recorded raw-schema sentinel/contract fixtures; unknown required shape maps to `PARSER.INVALID_OUTPUT`. |
| P-03 | Code license is approved while model weights or dependencies impose incompatible conditions. | Production legal exposure. | Separate code/weight/dependency/dataset inventory and approval ID; readiness fails closed if absent. Already reflected; retain as release gate. |
| P-04 | Two parsers use the label “table” with different semantics and confidence units. | Incorrect type/confidence merge. | Adapter envelope declares confidence semantics; normalizer maps types; calibrated validator evidence—not raw labels/max score—decides replacement. |
| P-05 | Cropped region removes heading/header/context needed to infer table/order, but full page would include unrelated content. | Bad local repair or scope leakage. | Planner declares bounded context padding/anchors; may supply read-only context artifacts while accepting outputs only inside target. Scope expansion is recorded and budgeted. |

## 6. RAG quality attacks

| ID | Attack / failure mode | Impact | Correction |
|---|---|---|---|
| Q-01 | Repeated heading prefixes look like source content with their own bbox. | Misleading citations and duplicate sparse terms. | Prefix is declared derived metadata and traces to heading blocks; no invented bbox. Already specified. |
| Q-02 | A giant merged-cell table cannot fit a row group without splitting the spanning cell. | Lost table semantics or token overflow. | Move boundary, carry contextual stub/header metadata, and fail with explicit issue if no legal hard-limit representation. Already specified; add test gate. |
| Q-03 | PARTIAL/degraded chunks are indexed like PASS chunks. | Retrieval serves known-corrupt evidence. | Chunk manifest carries quality/issue policy; downstream activation requires explicit acceptance, and missing gaps cannot be bridged. Clarify export activation. |
| Q-04 | Any IR revision change produces all-new chunk IDs and re-embedding cost even when most content is identical. | Expensive index churn. | Add content/embedding-input digests for safe cache reuse while keeping revision-specific chunk IDs/provenance. Cache reuse is tenant/policy scoped and never reuses stale metadata. |
| Q-05 | Chunk policy is tuned to a visible retrieval query set and appears better while general continuity degrades. | Evaluation overfit and poor new-domain retrieval. | Protected document-family split, boundary/continuity/provenance gates and retrieval proxy separated from parse quality already address this. |

## 7. Corrections required before Phase 1 approval

1. Add lossless IR packaging/sharding profile and storage paths.
2. Add `TextSpan`, table-cell fragments and non-identity semantic fingerprint guidance.
3. Add quality algorithm/index budgets.
4. Add GPU model residency scheduling and explicit scale migration triggers.
5. Strengthen batch cardinality/lease watchdog/snapshot consistency.
6. Add chunk content/embedding digests and PARTIAL activation policy.

These corrections are incorporated into the authoritative Specs after this review. Implementation acceptance tests in `IMPLEMENTATION_PLAN.md` and `TEST_STRATEGY.md` must enforce them.

## 8. Remaining accepted risks

- Single-host MVP is an availability and strong-tenant-isolation boundary.
- Parser/model quality is unknown until the local Golden Dataset is curated and run.
- Quality rules can have slice-specific false positives/negatives.
- Conservative merge may leave repairable defects unresolved; this is preferred to corrupting correct primary content.
- OCR/model/container dependency supply-chain and GPU driver risk cannot be eliminated, only constrained and observed.

## 9. Review verdict

The architecture is suitable to proceed to Phase 2 **only after explicit user approval and only as an incremental implementation**. Parser defaults are candidates, not accepted production choices. Phase 15 benchmark/promotion is required before production designation.


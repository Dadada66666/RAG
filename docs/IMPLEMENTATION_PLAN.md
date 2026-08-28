# Incremental Implementation Plan

| Field | Value |
|---|---|
| Status | Proposed after architecture approval |
| Rule | Do not start until explicit `PHASE 2 — IMPLEMENTATION` approval |
| Increment invariant | After every phase: install succeeds, smoke command runs, prior tests pass, artifacts/contracts remain compatible |

## 1. Delivery rules

- Each phase is a reviewable vertical or contract increment sized for one coding agent to implement and verify independently.
- Phase acceptance is binary. Deferred tasks move to a later phase; partially implemented hidden paths are disabled by config.
- No real adapter is allowed into orchestration before its contract/recorded-fixture tests pass.
- Generated schemas/OpenAPI are committed and diff-reviewed.
- Network/model downloads are never required by default tests.
- Any contract deviation discovered during implementation updates the relevant Spec/ADR before code merges.
- Default tools: Python 3.12+, Pydantic v2, pytest/Hypothesis, Ruff, pyright or mypy, FastAPI, SQLite, Docker. Dependency versions are locked.

## Phase 0 — Repository bootstrap and executable shell

### Goal

Create a minimal installable project with quality gates and a useful no-op CLI, without domain implementation.

### Files

`pyproject.toml`, lock file, `src/docparser/{__init__,version,cli/main}.py`, `tests/unit/test_version.py`, `configs/default.yaml`, CI/lint/type configuration, `README.md`.

### Interfaces

`docparser --version`, `docparser doctor --config ...` validating only bootstrap configuration.

### Implementation tasks

- Package/entry point and deterministic version reporting.
- Strict settings loader skeleton with unknown-key rejection.
- Ruff, type checker, pytest markers and coverage configuration.
- CI for install/lint/type/unit/docs presence; reproducible developer commands.

### Tests

Clean install, CLI exit codes/help/version, config unknown/missing field, no-network unit suite.

### Acceptance criteria

`docparser --version`, lint, type check and pytest pass from a clean environment; no parser/GPU dependency installed by default.

### Dependencies

Approved Phase 1 Specs.

### Risks

Tool churn or excessive dependencies. Mitigate with a minimal core extra and separate adapter/dev extras.

## Phase 1 — Canonical IR value types and schema spine

### Goal

Implement strict IDs, enums, geometry, page/block/provenance and document shell with deterministic serialization.

### Files

`src/docparser/ir/{models,geometry,ids,serialization}.py`, `schemas/document-ir/v1/document-ir.schema.json`, `tests/{unit/ir,schema}/`.

### Interfaces

`DocumentIR.model_validate`, `dump_canonical_json`, `validate_ir` and schema generation command.

### Implementation tasks

- Strict Pydantic base config; core fields from `DOCUMENT_IR_SPEC.md`.
- Coordinate/finite/bounds rules, UUID providers and canonical JSON/hash.
- Deterministic schema generation and drift check.

### Tests

Positive/negative schema fixtures, coordinate/rotation properties, ID determinism/collisions, Unicode/canonical hash vectors.

### Acceptance criteria

Minimal one-page IR round-trips and validates against committed schema; unknown fields/NaN/broken provenance fail; bootstrap CLI still runs.

### Dependencies

Phase 0.

### Risks

Pydantic coercion or JSON Schema mismatch. Use strict mode and schema/runtime cross-tests.

## Phase 2 — Complete IR graphs, tables, chunks and migration harness

### Goal

Complete schema 1.0 entities/referential invariants and establish migration discipline before parsers exist.

### Files

`src/docparser/ir/{tables,relationships,invariants,migrations}.py`, full example fixture, migration registry/tests.

### Interfaces

`validate_document_invariants`, `migrate_ir(source_version, target_version)`, provenance resolver.

### Implementation tasks

- Sections/tables/segments/cells/figures/equations/references/chunks/relationships.
- Text spans, table-cell fragments, semantic fingerprints and monolithic/sharded packaging manifest.
- Referential, reading-order, table-grid, section-tree and provenance reachability checks.
- No-op 1.0 migration harness and compatibility manifest.

### Tests

Full spec JSON example, broken refs/cycles/spans/pages, provenance graph and migration idempotency/round-trip.

### Acceptance criteria

Complete example validates; every hard invariant has a failing fixture; schema generation has no unreviewed diff.

### Dependencies

Phase 1.

### Risks

Over-modeling. Implement only fields/enums in the approved 1.0 Spec; use bounded extensions.

## Phase 3 — Local immutable artifact store

### Goal

Persist original/intermediate/canonical/derived artifacts safely on local disk through the storage port.

### Files

`src/docparser/ports/artifacts.py`, `adapters/storage/local.py`, `domain/artifacts.py`, storage contract tests.

### Interfaces

`ArtifactStore`, streaming `ArtifactWriter`, `ArtifactMetadata`.

### Implementation tasks

- Validated root, random temp, stream hash/size limit, fsync/atomic seal, immutable reads.
- Manifest-compatible artifact metadata and startup reconciliation primitives.
- Scratch namespace and safe cleanup API.

### Tests

Shared contract, short write/checksum/disk/permission simulation, path traversal/symlink, concurrent same-content write and crash orphans.

### Acceptance criteria

An IR fixture can be sealed/read/verified; no operation escapes temp test root; sealed artifacts cannot be overwritten.

### Dependencies

Phase 0; uses Phase 2 fixtures.

### Risks

Platform-specific atomic/fsync behavior. Document supported OS semantics and test Linux in CI.

## Phase 4 — SQLite job state, idempotency, leases and checkpoints

### Goal

Create the durable control plane and legal state machine independently of parsing.

### Files

`src/docparser/domain/{jobs,errors}.py`, `ports/jobs.py`, `adapters/jobs/sqlite.py`, migrations and state/integration tests.

### Interfaces

`JobRepository`, `LeaseCheckpointStore`, transition command/result, checkpoint compatibility key.

### Implementation tasks

- WAL/schema migrations, compare-and-set state version, fenced leases.
- Submission idempotency/coalescing, attempts, cancellation and DLQ metadata.
- Artifact/revision/manifest transaction references without large payloads.

### Tests

Transition table, replay/conflict, lease expiry/fence, busy retry, cancellation races, checkpoint reuse/invalidation and database restart.

### Acceptance criteria

A synthetic job traverses legal states, resumes after process restart and rejects stale/illegal commits; prior tests pass.

### Dependencies

Phases 0 and 3.

### Risks

Custom scheduler correctness and SQLite contention. Keep transactions short and property-test state/fencing.

## Phase 5 — Parser contract, fake adapter and first vertical CLI

### Goal

Deliver the earliest end-to-end runnable ingestion using a deterministic fake parser: source -> job -> ParseResult -> minimal IR -> quality stub PASS -> export.

### Files

`ports/parsers.py`, `domain/parser_contract.py`, `adapters/parsers/fake/`, `application/{submit,orchestrator}.py`, `normalization/base.py`, CLI parse/status/export, contract tests.

### Interfaces

`DocumentParser`, descriptors/capabilities, `ParseRequest/ParseResult/PageParseResult/ParserError`; orchestrator stage handlers.

### Implementation tasks

- Strict adapter envelope and option schema.
- Fake good/partial/timeout/OOM/malformed modes.
- Composition root, artifact/checkpoint commit order and local synchronous worker executor.
- Minimal quality stub clearly restricted to test profile; production profile disabled until Phase 9.

### Tests

Full fake adapter contract; CLI one-page happy path, idempotent replay, partial/error and restart-resume.

### Acceptance criteria

`docparser parse` on a test fixture completes and exports valid provenance-linked IR; no parser-specific branch exists outside adapter; system remains explicit non-production.

### Dependencies

Phases 2–4.

### Risks

Test stub leaking into production. Require profile gate and readiness refusal for fake adapter outside test environment.

## Phase 6 — Secure PDF admission and preflight

### Goal

Implement bounded PDF upload/local-source admission and deterministic `DocumentProfile` used by routing.

### Files

`preflight/{models,analyzer,rules}.py`, PDF tool adapter, admission application service, security fixtures/tests.

### Interfaces

`DocumentProfile`, `PreflightAnalyzer`, admission limits and typed input errors.

### Implementation tasks

- Stream signature/MIME/hash/size validation; filename sanitization.
- Page count/dimensions/rotation/encryption/text/image/font/density sampling and classification.
- Persist versioned profile, page signals and source artifact; sandbox wrapper/limits.
- Wire preflight/checkpoint into vertical pipeline.

### Tests

Born-digital/scanned/mixed/rotated/encrypted/corrupt/extreme-size fixtures, tool timeout/crash, no execution of PDF actions.

### Acceptance criteria

Supported PDF produces deterministic profile; invalid/limit inputs fail safely before parser; fake vertical path uses profile and resumes after it.

### Dependencies

Phase 5.

### Risks

PDF library native vulnerabilities and scan misclassification. Sandbox/limits and retain per-page uncertainty.

## Phase 7 — Docling adapter and recorded-output normalizer

### Goal

Add the proposed primary candidate without yet enabling broad production routing.

### Files

`adapters/parsers/docling/{adapter,runtime,mapping,options}.py`, `normalization/docling.py`, adapter extra/locked image, recorded fixtures.

### Interfaces

Existing parser contract; Docling namespaced option schema and normalizer plug-in.

### Implementation tasks

- Pin exact Docling/models/licenses; offline model cache and worker health self-test.
- Map document/page/block/table/figure/equation/order/raw confidence/coordinates.
- Preserve raw output, source IDs and transforms; handle partial page errors.
- One-page real model smoke behind GPU/adapter marker.

### Tests

Contract suite on recorded fixtures, raw schema change sentinel, zh/en/two-column/table/rotation mappings, timeout/OOM/invalid output and optional real smoke.

### Acceptance criteria

Recorded outputs normalize to valid complete IR with 100% provenance; real smoke is reproducible offline on approved image; adapter disabled if model/license manifest missing.

### Dependencies

Phases 5–6 and license/security approval.

### Risks

Frequent upstream releases and model-license variation. Pin digests and make raw fixture/schema sentinel a promotion gate.

## Phase 8 — Multipage primary pipeline and normalization completion

### Goal

Enable Docling primary for bounded multipage jobs with checkpoints, sections, repeated margins and cross-page entity normalization.

### Files

`normalization/{pipeline,sections,tables,reading_order,provenance}.py`, orchestrator primary handlers and integration fixtures.

### Interfaces

`Normalizer`, `NormalizationRequest/Result`, dependency/invalidation manifest.

### Implementation tasks

- Page-batch execution/checkpoint/resume and memory-bounded IR assembly.
- Stream page/entity shards for large inputs; validate exact batch cardinality and per-page digests before checkpoints.
- Canonical IDs, section forest, relationships, table segments and parser metadata.
- Complete invariants before revision commit; no quality acceptance yet.

### Tests

500-page synthetic resume, multipage/cross-page tables, margins/footnotes/captions/order, coordinate transforms and worker crash at batch boundaries.

### Acceptance criteria

Multipage primary revision is valid/reproducible, failure replays at most one configured unit, and model remains resident across documents.

### Dependencies

Phase 7.

### Risks

Memory grows with pages and cross-page invalidation is broad. Use artifact-backed fragments and explicit dependency graph.

## Phase 9 — Quality rules, scoring and publication gate

### Goal

Replace quality stub with ruleset 1.0 and prevent “parser complete” from becoming “document accepted” automatically.

### Files

`quality/{models,engine,scoring,recommendations,rules/...}.py`, quality report schema/fixtures and rule tests.

### Interfaces

`QualityRule`, `QualityValidator`, `QualityIssue/Report`, `FallbackRecommendation`.

### Implementation tasks

- Mandatory invariants and initial completeness/text/layout/table/structure/provenance catalog.
- Spatial/inverted indexes plus per-rule operation/evidence budgets; no unbounded all-pairs scans.
- Deterministic aggregation/threshold/status and skipped-rule handling.
- Persist report and gate POSTPROCESSING/PARTIAL/FAIL; fallback still reports “unavailable” until Phase 10.

### Tests

Rule unit/property/golden defects, score vectors, hard-gate override, language slices, mandatory timeout and report determinism.

### Acceptance criteria

Known bad complete parse is rejected/degraded with stable issue/evidence; valid fixtures pass; no LLM/network; fake/Docling vertical paths use real gate.

### Dependencies

Phases 6 and 8.

### Risks

False positives block documents; false negatives publish defects. Start conservative, expose evidence and calibrate before threshold freeze.

## Phase 10 — PaddleOCR-VL fallback adapter and planner

### Goal

Create issue-to-minimal-target planning and execute isolated fallback candidates, without applying merges yet.

### Files

`adapters/parsers/paddleocr_vl/`, `normalization/paddleocr_vl.py`, `fallback/{planner,materialize,budgets}.py`, recorded fixtures.

### Interfaces

`FallbackPlan/Target`, capability/benchmark profile, existing parser contract.

### Implementation tasks

- Pin PaddleOCR 3.7.0/PaddleOCR-VL 1.6/model/license/image.
- Page/region/table crop rendering and exact affine transforms.
- Capability filtering/ranking, target coalescing, budgets/fingerprints/loop prevention.
- Persist candidate fragments and diagnostics; mark merge pending/rejected, never modify baseline.

### Tests

Adapter contract, out-of-scope rejection, crop/rotation transforms, no capable parser, budgets/coalescing/whole-document justification and candidate partial/error.

### Acceptance criteria

Page 23 table issue schedules only authorized scope and yields valid isolated candidate; unrelated pages are not parsed; baseline remains unchanged.

### Dependencies

Phase 9 and license/security approval.

### Risks

Region lacks context, GPU OOM/model coexistence and hardware backend variance. Add padding policy, resource classes and benchmark VRAM before concurrent residency.

## Phase 11 — Transactional fallback merge and revalidation

### Goal

Apply only demonstrably better candidate fragments with complete identity/provenance/relationship handling.

### Files

`fallback/{matching,comparison,merge,dedup,ordering,transactions}.py`, merge report models and golden/failure tests.

### Interfaces

`MergeRequest/Result`, matcher strategy, quality comparator and copy-on-write revision commit.

### Implementation tasks

- Sparse weighted matching, deterministic assignment, one-to-one/split/merge/add/remove classification.
- Atomic table and figure/caption strategies, boundary anchors, duplicate suppression and local order rebuild.
- Changed-scope + dependency revalidation, minimum gain and fenced baseline commit.

### Tests

All 20 edge cases in `FALLBACK_SPEC.md`, property tests, stale/concurrent/crash transactions, no-gain reject and non-target no-regression.

### Acceptance criteria

Known table defect becomes a new valid revision with positive score delta/provenance; rejected/conflict/crash leaves baseline active and intact.

### Dependencies

Phase 10.

### Risks

False entity match and relationship corruption. Conservative thresholds, atomic groups and hard revalidation are non-negotiable.

## Phase 12 — RAG chunking and derived exports

### Goal

Produce versioned semantic chunks plus Markdown/HTML/JSON artifacts from accepted/approved-partial IR.

### Files

`chunking/{models,units,packing,tables,renderers}.py`, `application/export.py`, export manifest and tests.

### Interfaces

`Chunker/ChunkPolicy`, tokenizer port, export renderer.

### Implementation tasks

- Section/heading-aware parent-child units, semantic overlap and protected boundaries.
- Table row groups, figure-caption, equation/code, footnote/reference policies.
- Token/provenance validation, deterministic IDs/manifests and incremental invalidation.

### Tests

Chinese/English token limits, headings, long paragraph/code, merged/cross-page tables, figures/equations, PARTIAL gaps and deterministic regeneration.

### Acceptance criteria

Chunks meet all hard limits, source/provenance resolve, no illegal table/boundary splits, and exports reproduce from IR revision alone.

### Dependencies

Phases 2, 9 and 11.

### Risks

Tokenizer churn and oversized atomic structures. Pin tokenizer and implement explicit type-specific splitting.

## Phase 13 — Async HTTP service and durable worker supervision

### Goal

Expose the approved local pipeline through `/v1` with cancellation/retry/backpressure and isolated long-lived workers.

### Files

`api/{app,routes,models,errors,auth}.py`, `worker/{supervisor,scheduler,protocol}.py`, OpenAPI, Docker/runtime config and integration tests.

### Interfaces

Endpoints in `API_SPEC.md`, worker capability/heartbeat/dispatch envelope.

### Implementation tasks

- Streaming multipart admission, resources/ETag/problem responses/tenant context.
- Scheduler polling/dispatch, worker leases/heartbeats/drain/recycle and model residency.
- Independent lease-renewal watchdog and residency groups; serialize model swap or co-reside only under benchmark-approved VRAM profile.
- Cancellation grace/hard kill, adaptive batch after OOM, queue/disk/GPU backpressure.
- Non-root/network-disabled parser container profile and startup reconciliation.

### Tests

API contracts, auth/tenant isolation, concurrent submit/idempotency, process-kill recovery, cancel/retry, OOM adaptation, sandbox/limit tests.

### Acceptance criteria

API returns 202 and survives API/worker restarts without illegal state/data loss; 500-page synthetic job resumes; models do not reload per document.

### Dependencies

Phases 6–12.

### Risks

Single-host scheduler races and sandbox/GPU constraints. Fenced commits and explicit deployment threat model gate release.

## Phase 14 — Production observability and operational hardening

### Goal

Make every stage measurable/supportable and complete cleanup/retention/runbook behavior.

### Files

`adapters/telemetry/`, instrumentation, dashboards/alerts/runbooks, artifact reconciler/retention tasks and tests.

### Interfaces

Telemetry port, audit event writer and admin-disabled adapter/model policy.

### Implementation tasks

- JSON events, OTel spans, bounded Prometheus metrics and resource telemetry.
- Redaction/cardinality enforcement, audit trail and correlation.
- Dashboards/alerts/runbooks; retention/deletion/orphan cleanup; backup/restore procedure.

### Tests

Telemetry schema/redaction/cardinality, alert queries, cleanup safety roots, audit immutability and backup/restore exercise.

### Acceptance criteria

Required job summary fields/metrics exist; injected failures are diagnosable without content leakage; disk/queue/GPU alerts and runbooks work.

### Dependencies

Phase 13.

### Risks

High-cardinality telemetry or sensitive data leakage. Fixed label allow-list and canary redaction tests block release.

## Phase 15 — Golden benchmark, regression gates and default promotion

### Goal

Make parser/pipeline selection evidence-based and decide whether the proposed defaults are production-eligible.

### Files

`tools/benchmark/`, `tests/golden/` manifests/schemas/tiny fixtures, metric implementations, report templates and CI workflows.

### Interfaces

`docparser benchmark`, benchmark manifest/report schemas and promotion gate result.

### Implementation tasks

- Dataset governance/splits/annotation validators.
- Text/layout/order/table/structure/provenance/RAG/reliability/performance metrics.
- Paired bootstrap/slice reports, visual overlays and regression gate evaluator.
- Reference GPU runs for Docling, PaddleOCR-VL fallback and approved challengers; document the promotion/rollback decision.

### Tests

Metric known-answer tests, dataset leakage/digest/license checks, incomplete-run false-pass prevention, baseline/candidate regression examples.

### Acceptance criteria

Full report answers parser comparison by slice; hard gates and reference capacity pass; exact images/models/config are pinned; default promotion has ADR amendment and rollback. Otherwise system remains pre-production with evidence-based blockers.

### Dependencies

Phases 7–14 and curated/approved Golden Dataset.

### Risks

Insufficient or biased truth set and expensive GPU CI. Prioritize protected critical slices, report uncertainty and separate smoke/nightly/release tiers.

## Phase 16 — V1 scale adapters (conditional, measurement-triggered)

### Goal

Remove measured single-host bottlenecks without changing domain contracts.

### Files

S3/MinIO artifact adapter, PostgreSQL job/lease adapter, distributed scheduler/worker deployment, optional Dramatiq broker adapter, migration tooling.

### Interfaces

Existing storage/job/parser/worker ports; no Canonical IR/API breaking change.

### Implementation tasks

- Copy/verify/cutover artifacts; SQLite-to-PostgreSQL migration.
- Multiple capability/resource-class workers, quotas and placement-aware scheduling.
- Add external broker only if dispatch metrics justify it; PostgreSQL remains job truth.

### Tests

Shared adapter contracts, migration/rollback, multi-worker fencing, lost/duplicate delivery, S3 consistency, horizontal performance/fault suite.

### Acceptance criteria

Measured target workload scales horizontally, duplicate/stale commits remain impossible, V1 benchmark has no quality regression and MVP data can roll back/read safely.

### Dependencies

Phase 15 plus a documented capacity trigger.

### Risks

Distributed complexity, migration downtime and cost. Keep feature conditional and rehearse rollback.

## 2. Cross-phase Definition of Done

For every phase:

- goal-specific acceptance criteria and tests pass locally/CI;
- clean install and `docparser --version`/relevant smoke command work;
- all prior tests pass; no hidden production flags/defaults changed;
- configuration/schema/API changes are validated, versioned and documented;
- typed failures, retry/idempotency and telemetry/redaction are addressed proportionally;
- new dependencies/models have digest, SBOM/license/security review;
- repository has no parser-specific imports outside its adapter;
- no phase leaves a migration/checkpoint/artifact in an unreadable intermediate state.

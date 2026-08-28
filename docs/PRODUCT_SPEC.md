# Enterprise Document Parsing & RAG Ingestion Platform — Product Specification

| Field | Value |
|---|---|
| Status | Proposed for Phase 1 review |
| Spec version | 0.1.0 |
| Last updated | 2026-08-28 |
| Scope | Architecture and contracts only; no production implementation |

## 1. Problem statement

Enterprise PDF ingestion is not equivalent to `PDF -> text` or `PDF -> Markdown`. A successful parser process can still lose pages, corrupt reading order, flatten merged cells, detach captions, or produce text that cannot be cited. Parser-specific output also creates a replacement and regression-testing trap.

The product shall convert untrusted, complex documents into a versioned Canonical Document IR and retrieval-ready chunks while preserving an auditable path from every derived item to the original page and region. It shall normally run one primary parser, validate the result independently, and invoke a complementary parser only for failed document/page/region/table/figure/block scopes.

## 2. Product principles

1. **Canonical IR is the product contract.** Markdown is an export, not the source of truth.
2. **Parser success is not quality success.** A validator makes a separate, recorded decision.
3. **Provenance is mandatory.** Untraceable content is incomplete content.
4. **Fallback is selective and budgeted.** Whole-document fallback is an explicit last resort.
5. **Adapters contain parser coupling.** Domain, API, storage and chunking code do not branch on parser names.
6. **Deterministic by default.** LLMs are not required by the core pipeline.
7. **Simple first, scalable by design.** MVP runs on one Linux host and one NVIDIA GPU without Kubernetes, Kafka, Temporal or a mandatory external queue.

## 3. Users and stakeholders

| Persona | Need |
|---|---|
| RAG platform engineer | Stable chunks, citations, reproducible ingestion and bulk APIs |
| Document AI engineer | Adapter contracts, raw artifacts, quality diagnostics and benchmark slices |
| Application developer | Async job API/CLI without parser-specific knowledge |
| Data/QA engineer | Golden datasets, visual diff artifacts and regression gates |
| SRE/platform operator | Resume, resource limits, metrics, traces and actionable errors |
| Security/compliance reviewer | Isolation, retention, tenant boundaries, license inventory and audit events |
| End user/reviewer | Correct reading order, tables and citations that navigate to the source |

## 4. Goals

- Parse born-digital, scanned and mixed PDFs in Chinese, English and bilingual layouts.
- Represent text, headings, lists, tables, figures, captions, equations, footnotes, repeated margins and reading order without binding consumers to a parser.
- Detect structural and content-quality failures through deterministic, heuristic and statistical validation.
- Repair only affected scopes and merge them transactionally with explicit provenance.
- Resume long documents from durable stage/page checkpoints.
- Produce structure-aware chunks suitable for dense, sparse and hybrid retrieval.
- Provide local CLI and asynchronous service modes over the same application layer.
- Compare parser and pipeline versions with a repeatable Golden Dataset benchmark.

## 5. Non-goals

- Building a retrieval engine, vector database, reranker, answer generator or document viewer in MVP/V1.
- Pixel-perfect reconstruction of the original PDF.
- General-purpose Office/email ingestion in MVP; interfaces must not preclude it.
- Handwriting, forms/KV extraction, semantic figure interpretation or LLM-based repair as guaranteed MVP capabilities.
- Human annotation tooling; benchmark manifests may reference external annotation tools.
- Multi-region active-active deployment, Kubernetes orchestration or exactly-once distributed execution.
- Guaranteeing identical output across nondeterministic GPU kernels; reproducibility means pinned inputs/config/models and recorded runtime, with declared tolerance.

## 6. Document scope

### MVP mandatory slices

- 1–500 page PDF; acceptance includes a 1000-page stress fixture.
- Born-digital, scanned, mixed text-layer/image pages.
- Chinese, English and mixed Chinese/English.
- Single-, double- and multi-column pages.
- Tables including merged cells and cross-page continuations.
- Figures and captions; display equations; heading hierarchy; header/footer/page numbers; footnotes.
- Rotation, noisy scans, malformed text encoding and partially corrupt inputs.

### Deferred slices

- Handwritten documents, signatures/seals as semantic objects, fillable forms and chart-to-data extraction.
- DOCX/PPTX/XLSX/images as first-class sources.
- Password acquisition workflow for encrypted PDFs.

## 7. Use cases

1. Parse a normal annual report with only the primary parser and export IR, Markdown and chunks.
2. Repair one malformed table on page 23 without reparsing the other 99 pages.
3. Resume a 500-page job after an OOM at page 430 from the latest compatible checkpoint.
4. Trace a retrieved chunk to source blocks, page coordinates, source PDF digest and parser runs.
5. Re-run the same document with a new parser/pipeline version without overwriting the old revision.
6. Benchmark parser A against parser B by corpus slice, quality, latency, VRAM and fallback rate.
7. Deduplicate a repeated submission while preserving tenant ownership and idempotent client semantics.
8. Cancel queued/running work at a safe checkpoint and clean ephemeral artifacts according to policy.

## 8. Functional requirements

| ID | Requirement | MVP | V1 | Future |
|---|---|:---:|:---:|:---:|
| FR-001 | MIME/signature validation, limits, encryption/corruption checks | Yes | Harden | Harden |
| FR-002 | Low-cost `DocumentProfile` preflight | Yes | Tune | Extend formats |
| FR-003 | Capability-discovered parser adapters | Yes | More adapters | Remote/commercial |
| FR-004 | Canonical IR normalization and JSON serialization | Yes | Migrations | Multi-format |
| FR-005 | Page/block/table provenance and coordinate transforms | Yes | Visual viewer links | Cross-document |
| FR-006 | Deterministic rule-based validation | Yes | Calibrated models | Optional semantic rules |
| FR-007 | Selective page/region/table/block fallback | Page/table/region | Figure/block refinement | Document repair planner |
| FR-008 | Transactional merge with conflict diagnostics | Yes | Learned calibration | Human review loop |
| FR-009 | Heading-aware, table-atomic RAG chunks | Yes | Parent-child policies | Multimodal chunks |
| FR-010 | Immutable artifacts and versioned exports | Yes | S3/MinIO | Lifecycle tiers |
| FR-011 | Resumable CLI execution | Yes | Batch CLI | Remote CLI |
| FR-012 | Async HTTP job API | Basic | Auth/quotas/webhooks | Multi-region |
| FR-013 | Durable jobs, retry, cancellation and DLQ state | Yes | Distributed workers | Cross-region |
| FR-014 | Structured logs, metrics and traces | Basic | Dashboards/alerts | Chargeback |
| FR-015 | Golden benchmark and regression report | Core | CI gates/visual diffs | Reviewer UI |
| FR-016 | Parser/config/model/version inventory | Yes | SBOM/license gates | Policy service |

## 9. Non-functional requirements

### Quality and integrity

- Zero silently missing pages in accepted output; page cardinality is a hard invariant.
- Every published content block, table, figure, equation and chunk has resolvable provenance.
- Canonical JSON validates against the pinned schema and rejects unknown unnamespaced fields.
- Fallback never mutates an accepted revision in place; merge creates a new revision.
- A pipeline change cannot pass release gates if a protected Golden Dataset slice regresses beyond its threshold.

### Reliability

- All stages are idempotent under the same `(tenant, source_digest, pipeline_version, normalized_config_hash)`.
- A worker crash loses at most the active checkpoint unit, never the completed document prefix.
- Retry classification is driven by the error taxonomy, not exception string matching.
- Timeouts, retry count, fallback scope/page budget and cancellation are configurable.
- Partial output is never labeled `COMPLETED`; it requires explicit `PARTIAL` policy and issue disclosure.

### Replaceability and maintainability

- A parser is replaceable by implementing adapter and contract tests; no consumer change is required.
- Storage is accessed through blob, metadata and lease/checkpoint ports.
- Pure normalization, validation, merge and chunking functions are testable without GPU/network.
- Versioned schemas and migrations are additive within a major version.

### Security and privacy

- PDF parsing/rendering occurs in a constrained worker with no ambient credentials or outbound network.
- Tenant identity is required in all persistent keys and authorization decisions.
- Original files and extracted content have explicit retention and deletion policies.
- Logs/metrics do not contain document text, filenames or high-cardinality tenant/document IDs by default.

## 10. Initial performance and reliability targets

These are **MVP engineering targets to validate on the reference host**, not vendor claims or contractual SLOs. `EVALUATION_SPEC.md` defines the workload and measurement method.

| Metric | MVP target | V1 target |
|---|---:|---:|
| Normal born-digital primary-only throughput | >= 1.0 page/s | >= 2.0 pages/s |
| Scanned/VLM throughput | Baseline and no unbounded degradation | >= 0.5 page/s on reference GPU |
| API submission P95, excluding upload | < 500 ms | < 250 ms |
| 100-page end-to-end P95 | Establish per-slice baseline | <= baseline + 10% unless quality improves |
| Peak host RAM | Bounded by configured page concurrency | <= configured budget, no corpus-size linear leak |
| Peak VRAM | <= 90% configured budget | <= 85% steady state |
| Warm worker model reloads | 0 per document | 0 per document |
| Resume replay | <= one checkpoint unit | Same |
| Job terminal-state durability | 100% in fault-injection suite | 100% |
| Accepted-output page completeness | 100% | 100% |
| Provenance resolvability | 100% on published entities | 100% |
| Primary-only rate | Measure by slice; target >= 70% overall after tuning | >= 80% without quality regression |

Targets must be reported with hardware, parser/model digest, renderer DPI, page mix and concurrency. No pages/sec number is portable without that context.

## 11. Success metrics

- Protected Golden Dataset gates pass for text, layout, table, structure, provenance and RAG readiness.
- At least 95% of valid MVP documents reach `COMPLETED` or policy-approved `PARTIAL`; non-retryable corrupt/unsupported inputs are excluded and reported separately.
- At least 99% of transient fault-injection jobs recover without manual artifact repair.
- Every failure exposes a stable error code, stage, scope, retryability and correlation ID.
- Default parser can be swapped with an adapter/config change and contract/benchmark run only.
- A reviewer can explain why fallback ran, what it replaced, and which source/parser contributed each accepted entity.

## 12. Release boundaries

### MVP

- Local filesystem + SQLite/WAL, one API process, one long-lived GPU worker.
- PDF only; Docling primary candidate; PaddleOCR-VL selective fallback candidate.
- Canonical IR 1.x; deterministic validators; selective page/region/table repair.
- JSON/JSONL, Markdown and RAG chunk exports.
- CLI, async API, checkpoints, basic Prometheus/OpenTelemetry integration, benchmark runner.

### V1

- S3/MinIO blob storage, PostgreSQL metadata/lease store, multiple CPU/GPU workers.
- Fine-grained quotas/backpressure, calibrated quality by document slice, visual regression artifacts.
- Additional parser adapters selected by benchmark and license review.
- Parent-child and multimodal chunk policies; webhooks and stronger tenant controls.

### Future

- Horizontally scaled schedulers/workers, multiple GPUs and placement-aware batching.
- Additional formats and optional semantic/LLM validation or rare repair behind explicit capability and policy flags.
- Human review workflows and active-learning dataset curation.

## 13. Product-level acceptance criteria for Phase 1

- All required specifications and six ADRs exist and cross-reference a consistent set of states, errors, versions and entities.
- A complete Canonical IR JSON example validates conceptually against the documented field rules.
- Parser selection is evidence-based, dated, licensed separately for code/weights and explicitly provisional pending local benchmark.
- Merge behavior, quality gates, checkpoint boundaries and partial-publication rules are deterministic enough to implement without architectural invention.
- Architecture self-review includes at least five risks in each required risk category and records resulting corrections.


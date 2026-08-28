# Phase 1 Architecture Package

This directory is the executable design contract for the Enterprise Document Parsing & RAG Ingestion Platform. Phase 1 contains no production parser implementation.

## Reading order

1. [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — scope, users, requirements and targets.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — system boundaries, diagrams, runtime/state/error design.
3. [`DOCUMENT_IR_SPEC.md`](DOCUMENT_IR_SPEC.md) — authoritative canonical data contract.
4. [`PARSER_ADAPTER_SPEC.md`](PARSER_ADAPTER_SPEC.md) — parser boundary, research and recommendation.
5. [`QUALITY_VALIDATION_SPEC.md`](QUALITY_VALIDATION_SPEC.md) — quality rules, score and repair decision.
6. [`FALLBACK_SPEC.md`](FALLBACK_SPEC.md) — selective target planning and transactional merge.
7. [`RAG_CHUNK_SPEC.md`](RAG_CHUNK_SPEC.md) — retrieval unit construction/provenance.
8. [`STORAGE_SPEC.md`](STORAGE_SPEC.md), [`API_SPEC.md`](API_SPEC.md), [`OBSERVABILITY_SPEC.md`](OBSERVABILITY_SPEC.md), [`SECURITY_SPEC.md`](SECURITY_SPEC.md) — platform contracts.
9. [`EVALUATION_SPEC.md`](EVALUATION_SPEC.md), [`TEST_STRATEGY.md`](TEST_STRATEGY.md) — evidence and gates.
10. [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) — adversarial review and corrections.
11. [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — independently verifiable Phase 2 increments.
12. [`adr/`](adr/) — durable architecture decisions.

## Key decisions

- Strict Pydantic v2 models author Canonical IR; generated committed JSON Schema is the wire contract.
- Canonical page coordinates are top-left, post-rotation CropBox points with reversible affine transforms.
- Parser execution success and quality acceptance are separate states/reports.
- Normal path runs one primary; fallback is issue-scoped, bounded and applied only after positive revalidation.
- MVP candidate pair is Docling primary plus PaddleOCR-VL 1.6 fallback, pending local benchmark/legal/security promotion.
- MVP uses local immutable artifacts + SQLite WAL and long-lived isolated workers; PostgreSQL/S3/distributed workers are measured V1 migrations.
- Core parsing/validation/chunking has no external LLM dependency.

## Phase 1 Definition of Done

- [x] Canonical Document IR, coordinates, IDs, provenance and migrations defined.
- [x] Parser adapter/capability/error contracts and current candidate research defined.
- [x] Primary/quality/selective fallback/merge strategy defined.
- [x] RAG chunk schema and structure-aware algorithm defined.
- [x] Storage abstraction, local/S3 path and database decision defined.
- [x] Job state machine, retry, checkpoint/resume, cancellation and idempotency defined.
- [x] API/CLI, observability, security and error taxonomy defined.
- [x] Golden Dataset, benchmark metrics/regression gates and test strategy defined.
- [x] Context/container/component/pipeline Mermaid diagrams completed.
- [x] Six required ADRs completed.
- [x] Incremental implementation phases, tests, dependencies, risks and acceptance criteria defined.
- [x] Contrarian architecture review contains at least five risks in every required category and corrections were applied.
- [x] Phase boundary preserved: no business pipeline code implemented.

## Approval boundary

Do not implement `IMPLEMENTATION_PLAN.md` until the user explicitly authorizes `PHASE 2 — IMPLEMENTATION`. Parser/model production promotion additionally requires Phase 15 Golden Dataset, license, security and reference-hardware gates.


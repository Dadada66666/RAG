# Architecture and Implementation Contracts

The active delivery contract is [COMPLEX_DOCUMENT_QA_SPEC.md](COMPLEX_DOCUMENT_QA_SPEC.md):
document-scoped and corpus-wide evidence QA, complete experiment accounting, table context recovery,
numeric evidence checks, and measured runtime quality. Its milestone table separates implemented
code from future work. [EVIDENCE_QA_GUIDE.md](EVIDENCE_QA_GUIDE.md) contains executable server commands.

Current code includes parsing, a deterministic quality gate, selective fallback, section materialization,
Fixed/Structure experimental chunkers, exact dense A/B, persisted evidence indexing and cited QA.
Real end-to-end answer quality remains unverified on this local machine. Historical platform and
hybrid-retrieval designs below are references, not a requirement to implement every module.

## Reading order

1. [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — scope, users, requirements and targets.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — boundaries, diagrams, runtime, state and errors.
3. [`DOCUMENT_IR_SPEC.md`](DOCUMENT_IR_SPEC.md) — authoritative Canonical Document IR.
4. [`PARSER_ADAPTER_SPEC.md`](PARSER_ADAPTER_SPEC.md) — neutral parser boundary and capabilities.
5. [`QUALITY_VALIDATION_SPEC.md`](QUALITY_VALIDATION_SPEC.md) — calibrated risk gate.
6. [`EVALUATION_SPEC.md`](EVALUATION_SPEC.md) — parsing benchmark, ParseBench and metric integrity.
7. [`FALLBACK_SPEC.md`](FALLBACK_SPEC.md) — selective planning, comparison and transactional merge.
8. [`RAG_CHUNK_SPEC.md`](RAG_CHUNK_SPEC.md) — derived parent-child/table retrieval units.
9. [`RAG_RETRIEVAL_SPEC.md`](RAG_RETRIEVAL_SPEC.md) — dense/sparse/RRF/rerank/context/citation.
10. [`RAG_EVALUATION_SPEC.md`](RAG_EVALUATION_SPEC.md) — parsing/retrieval/answer evaluation layers.
11. [`STORAGE_SPEC.md`](STORAGE_SPEC.md), [`API_SPEC.md`](API_SPEC.md), [`OBSERVABILITY_SPEC.md`](OBSERVABILITY_SPEC.md), [`SECURITY_SPEC.md`](SECURITY_SPEC.md) — platform contracts.
12. [`TEST_STRATEGY.md`](TEST_STRATEGY.md) — contract and regression test policy.
13. [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) and [`NEXT_PHASE_ARCHITECTURE_REVIEW.md`](NEXT_PHASE_ARCHITECTURE_REVIEW.md) — adversarial reviews and implementation-state audit.
14. [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — historical phases and prioritized quality track.
15. [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md) and [`adr/`](adr/) — decision deadlines and durable decisions.

## Current decisions

- Strict Pydantic v2 models author the versioned Canonical IR; generated JSON Schema is the wire contract.
- Canonical coordinates are top-left, post-rotation CropBox points with reversible transforms.
- Parser success and Quality Gate acceptance are separate; the gate is a risk gate, not a correctness oracle.
- Docling and PaddleOCR-VL 1.6 are candidates; neither is permanently promoted without corrected local evidence.
- Native PDF text is evidence, not ground truth.
- Official ParseBench, ParseBench-derived subset and Project Golden Dataset metrics are never mixed.
- Parent-child and structure-aware chunking are implemented experiments, without a superiority claim.
- Current QA keeps Fixed/dense retrieval and tests structural context after retrieval. Hybrid and
  reranking are deferred; they are not part of the current controlled experiment.
- Core parsing, validation and chunking do not depend on an external LLM.

## Current implementation boundary

Use the milestone/acceptance table in [COMPLEX_DOCUMENT_QA_SPEC.md](COMPLEX_DOCUMENT_QA_SPEC.md)
for current work. The older implementation plan records previous phases. No accuracy claim is valid
until independently judged real model/document experiments have run.

# ADR-003: Primary Parser plus Quality-Gated Selective Fallback

## Status

Proposed — 2026-08-28

## Context

Running several parsers serially on all pages multiplies latency/GPU cost and complicates disagreement while still providing no quality proof. A single parser can silently lose content. Long documents need localized recovery.

## Decision

Run one primary parser in normal flow, normalize to IR, validate independently with deterministic/heuristic/statistical rules, and invoke a complementary parser only for issue-derived document/page/region/table/figure/block scopes. Merge copy-on-write through versioned matching/quality comparison and revalidation. Bound rounds/pages/area/time/cost. Whole-document fallback requires explicit planner justification.

MVP candidates are Docling primary and PaddleOCR-VL 1.6 fallback, conditional on local benchmark and legal/security promotion gates.

## Alternatives

### All parsers on all pages and vote

High cost/latency, hard confidence calibration and no clear truth. Rejected.

### Single parser with no validator

Operational success is mistaken for semantic correctness. Rejected.

### Fallback whole document on any failure

Simple merge but destroys useful checkpoints and scales poorly. Rejected.

### LLM judge/repair by default

Nondeterministic, costly, hard to cite and an external dependency. Rejected for core; optional future advisory enhancement.

## Consequences

Positive: normal documents pay one-parser cost, repair is explainable/local, and complementary parsers can evolve independently.

Negative: validator false positives/negatives and merge correctness are major risks; a fallback model remains resident/cold-start managed; local scope may lack context.

## Migration path

Calibrate rules/thresholds on Golden Dataset, add benchmark profiles by issue/slice, support additional fallback adapters, and introduce policy routing only through capabilities/config. Rollback disables fallback or restores prior pipeline revision without changing IR schema.


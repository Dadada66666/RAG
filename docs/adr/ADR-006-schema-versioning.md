# ADR-006: Independent Semantic Versioning and Immutable Revisions

## Status

Proposed — 2026-08-28

## Context

Parser, model, Canonical IR, pipeline, validator, merge and chunk behavior change at different rates. One application version cannot explain or reproduce output. In-place upgrades would invalidate citations and comparisons.

## Decision

Version independently:

```text
IR schema, adapter contract, adapter/parser/model, pipeline,
normalizer, validator ruleset, merge algorithm, chunk schema/chunker/tokenizer,
renderer, config hash and benchmark dataset/runner.
```

Apply SemVer to public wire/behavior contracts, pin immutable dependency/model/container digests, and create immutable IR/chunk/report revisions. Schema migrations are pure, tested and create new artifacts/revisions with lineage. Unknown unnamespaced fields are rejected; bounded namespaced extensions are preserved.

## Alternatives

### Single application version

Easy display but cannot identify component changes or selective invalidation. Rejected.

### Mutable document row updated in place

Simple reads but destroys reproducibility, citations and rollback. Rejected.

### Timestamp-only/versionless JSON

Cannot validate compatibility or automate migrations. Rejected.

### Accept any fields for forward compatibility

Avoids rejections but silently changes semantics and enables unbounded parser leakage. Rejected; namespaced extensions provide controlled flexibility.

## Consequences

Positive: precise provenance, reproducible benchmarks, selective checkpoint invalidation and safe rollback.

Negative: version matrix and migrations require discipline; storage retains multiple revisions; promotion tooling must validate compatibility.

## Migration path

Support current major plus agreed prior minors, provide deterministic migration registry, retain prior active manifest for rollback and garbage-collect old revisions only under retention policy. Any coordinate/ID/meaning change requires a major schema ADR.

## Compatibility note — 2026-09-01

The current V1 writer is `1.1.0`. This minor release adds `QualityStatus.NOT_EVALUATED` and allows `QualitySummary.score` and `quality_report_id` to be null only before validator execution. It also enforces that table-cell fragments spatially overlap their referenced segment with a `0.25 pt` tolerance. The reader migrates `1.0.0` payloads to `1.1.0` deterministically; existing evaluated summaries are preserved and no missing quality evidence is invented. The committed schema remains under the stable `schemas/document-ir/v1/` family.

## Compatibility note — 2026-09-02

The current V1 writer is `1.2.0`. This additive lifecycle amendment allows evaluated
`PASS/DEGRADED/FAIL` summaries to use `score=null` when the validator uses discrete decisions
rather than a calibrated continuous score. An evaluated summary still requires a
`quality_report_id`; `NOT_EVALUATED` still forbids both report ID and score and remains
non-publishable. The deterministic registry migrates `1.1.0 -> 1.2.0` by changing only
`schema_version`; existing quality values are preserved. Historical V1.1 semantics are not
reinterpreted.
